# -*- coding: utf-8 -*-

# Copyright 2018, IBM.
#
# This source code is licensed under the Apache License, Version 2.0 found in
# the LICENSE.txt file in the root directory of this source tree.

# pylint: disable=abstract-method,too-many-ancestors
"""

Contains a (slow) python sympy-based simulator.

It produces the state vector in symbolic form.
In particular, it simulates the quantum computation with the sympy APIs,
which preserve the symbolic form of numbers, e.g., sqrt(2), e^{i*pi/2}.

How to use this simulator:
see examples/using_sympy_provider_level_0.py

Example output:
final quantum amplitude vector: [sqrt(2)/2 0 0 sqrt(2)/2]

Advantages:
1. The tool obviates the manual calculation with a pen and paper, enabling
 quick adjustment of your prototype code.
2. The tool leverages sympy's symbolic computational power to keep the most
leverages sympy's simplification engine to simplify the expressions as much as possible.
3. The tool supports u gates, including u1, u2, u3, cu1, cu2, cu3.

Analysis of results and limitations:
1. It can simplify expressions, including complex ones such as sqrt(2)*I*exp(-I*pi/4)/4.
2. It may miss some simplification opportunities.
For instance, the amplitude
"0.245196320100808*sqrt(2)*exp(-I*pi/4) - 0.048772580504032*sqrt(2)*I*exp(-I*pi/4)"
can be further simplified.
3. It may produce results that are hard to interpret.
4. Memory error may occur if there are many qubits in the system.
This is due to the limit of classical computers and show the advantage of the quantum hardware.

Warning: it is slow.
Warning: this simulator computes the final amplitude vector precisely within a single shot.
Therefore we do not need multiple shots.
"""

import logging
import uuid
import time
import numpy as np
from sympy import Matrix, pi, I, exp
from sympy import re, im
from sympy.physics.quantum.gate import H, X, Y, Z, S, T, CNOT, IdentityGate, OneQubitGate, CGate
from sympy.physics.quantum.qapply import qapply
from sympy.physics.quantum.qubit import Qubit
from sympy.physics.quantum.represent import represent

from qiskit.backends import BaseBackend
from qiskit.qobj import Result as QobjResult, ExperimentResult
from qiskit.result import Result

from . import __version__
from .simulatortools import compute_ugate_matrix
from .sympysimulatorerror import SympySimulatorError
from .sympyjob import SympyJob

logger = logging.getLogger(__name__)


class SDGGate(OneQubitGate):
    """implements the SDG gate"""
    gate_name = 'SDG'

    def get_target_matrix(self, format='sympy'):
        """Return the Matrix that corresponds to the gate.

        Returns:
            Matrix: the matrix that corresponds to the gate.
                    Matrix is a type from sympy.
                    Each entry in it can be in the symbolic form.
        """
        # pylint: disable=redefined-builtin,unused-argument
        return Matrix([[1, 0], [0, -I]])


class TDGGate(OneQubitGate):
    """implements the TDG gate"""
    gate_name = 'TDG'

    def get_target_matrix(self, format='sympy'):
        """Return the Matrix that corresponds to the gate.

        Returns:
            Matrix: the matrix that corresponds to the gate
        """
        # pylint: disable=redefined-builtin,unused-argument
        return Matrix([[1, 0], [0, exp(-I*pi/4)]])


class UGateGeneric(OneQubitGate):
    """implements the general U gate"""
    _u_mat = None
    gate_name = 'U'

    def set_target_matrix(self, u_matrix):
        """this API sets the raw matrix that corresponds to the U gate
            the client should use this API whenever she creates a UGateGeneric object!
            Args:
                u_matrix (Matrix): set the matrix that corresponds to the gate
        """
        self._u_mat = u_matrix

    def get_target_matrix(self, format='sympy'):
        """return the Matrix that corresponds to the gate
        Returns:
            Matrix: the matrix that corresponds to the gate
        """
        # pylint: disable=redefined-builtin,unused-argument
        return self._u_mat


class SympyStatevectorSimulator(BaseBackend):
    """Sympy implementation of a statevector simulator."""

    DEFAULT_CONFIGURATION = {
        'name': 'statevector_simulator',
        'url': 'https://github.com/Qiskit/qiskit-addon-sympy',
        'simulator': True,
        'local': True,
        'description': 'A sympy-based statevector simulator',
        'coupling_map': 'all-to-all',
        'basis_gates': 'u1,u2,u3,cx,id'
    }

    def __init__(self, configuration=None, provider=None):
        """Initialize the SympyStatevectorSimulator object.

        Args:
            configuration (dict): backend configuration
            provider (SympyProvider): parent provider
        """
        super().__init__(configuration or self.DEFAULT_CONFIGURATION.copy(),
                         provider=provider)

        self._number_of_qubits = None
        self._statevector = None

    @staticmethod
    def _conjugate_square(com):
        """simpler helper for returning com*conjugate(com), where com is a complex number
            Args:
                com (object): a complex number
            Returns:
                object: com*conjugate(com)
        """
        return im(com)**2 + re(com)**2

    def run(self, qobj):
        """Run qobj asynchronously.

        Args:
            qobj (QObj): QObj structure

        Returns:
            SympyJob: derived from BaseJob
        """
        job_id = str(uuid.uuid4())
        sym_job = SympyJob(self, job_id, self._run_job, qobj)
        sym_job.submit()
        return sym_job

    def _run_job(self, job_id, qobj):
        """Run circuits in qobj and return the result

            Args:
                qobj (Qobj): Qobj structure
                job_id (str): A job id

            Returns:
                qiskit.Result: Result is a class including the information to be returned to users.
                    Specifically, result_list in the return contains the essential information,
                    which looks like this::

                        [{'data':
                        {
                          'statevector': array([sqrt(2)/2, 0, 0, sqrt(2)/2], dtype=object),
                        },
                        'status': 'DONE'
                        }]
        """
        self._validate(qobj)
        result_list = []
        start = time.time()
        for circuit in qobj.experiments:
            result_list.append(self.run_circuit(circuit))
        end = time.time()

        # Build a schema-conformant container of the results.
        result = {'backend_name': self.name(),
                  'backend_version': __version__,
                  'qobj_id': qobj.qobj_id,
                  'job_id': job_id,
                  'results': result_list,
                  'status': 'COMPLETED',
                  'success': True,
                  'time_taken': (end - start)}
        qobj_result = QobjResult(**result)

        # Return a qiskit.Result object.
        return Result(qobj_result)

    def run_circuit(self, circuit):
        """Run a circuit and return object.

        Args:
            circuit (QobjExperiment): Qobj experiment
        Returns:
            ExperimentResult: Container for a single experiment::

                {
                "data":{
                        'statevector': array([sqrt(2)/2, 0, 0, sqrt(2)/2], dtype=object)},
                "status": --status (string)--
                }

        Raises:
            SympySimulatorError: if an error occurred.
        """
        self._number_of_qubits = circuit.header.number_of_qubits
        self._statevector = 0

        self._statevector = Qubit(*tuple([0]*self._number_of_qubits))
        for operation in circuit.instructions:
            if getattr(operation, 'conditional', None):
                raise SympySimulatorError('conditional operations not supported '
                                          'in statevector simulator')
            if operation.name in ('measure', 'reset'):
                raise SympySimulatorError(
                    'operation {} not supported by sympy statevector simulator.'.format(
                        operation.name))
            if operation.name in ('U', 'u1', 'u2', 'u3'):
                qubit = operation.qubits[0]
                opname = operation.name.upper()
                opparas = getattr(operation, 'params', None)
                _sym_op = SympyStatevectorSimulator.get_sym_op(opname, tuple([qubit]), opparas)
                _applied_statevector = _sym_op * self._statevector
                self._statevector = qapply(_applied_statevector)
            elif operation.name == 'id':
                logger.info('Identity gate is ignored by sympy-based statevector simulator.')
            elif operation.name == 'barrier':
                logger.info('Barrier is ignored by sympy-based statevector simulator.')
            elif operation.name in ('CX', 'cx'):
                qubit0 = operation.qubits[0]
                qubit1 = operation.qubits[1]
                opname = operation.name.upper()
                opparas = getattr(operation, 'params', None)
                q0q1tuple = tuple([qubit0, qubit1])
                _sym_op = SympyStatevectorSimulator.get_sym_op(opname, q0q1tuple, opparas)
                self._statevector = qapply(_sym_op * self._statevector)
            else:
                backend = self.name
                err_msg = '{0} encountered unrecognized operation "{1}"'
                raise SympySimulatorError(err_msg.format(backend, operation.name))

        matrix_form = represent(self._statevector)
        shape_n = matrix_form.shape[0]
        list_form = [matrix_form[i, 0] for i in range(shape_n)]

        # Build a schema-conformant container of the Experiment results.
        result = {
            'data': {'statevector': np.asarray(list_form)},
            'success': True,
            'shots': 1,
            'status': 'DONE',
            'header': {'name': circuit.header.name}
        }

        return ExperimentResult(**result)

    @staticmethod
    def get_sym_op(name, qid_tuple, params=None):
        """ return the sympy version for the gate
        Args:
            name (str): gate name
            qid_tuple (tuple): the ids of the qubits being operated on
            params (list): optional parameter lists, which may be needed by the U gates.
        Returns:
            object: (the sympy representation of) the gate being applied to the qubits
        Raises:
            SympySimulatorError: if an unsupported operation is seen
        """
        the_gate = None
        if name == 'ID':
            the_gate = IdentityGate(*qid_tuple)  # de-tuple means unpacking
        elif name == 'X':
            the_gate = X(*qid_tuple)
        elif name == 'Y':
            the_gate = Y(*qid_tuple)
        elif name == 'Z':
            the_gate = Z(*qid_tuple)
        elif name == 'H':
            the_gate = H(*qid_tuple)
        elif name == 'S':
            the_gate = S(*qid_tuple)
        elif name == 'SDG':
            the_gate = SDGGate(*qid_tuple)
        elif name == 'T':
            the_gate = T(*qid_tuple)
        elif name == 'TDG':
            the_gate = TDGGate(*qid_tuple)
        elif name == 'CX' or name == 'CNOT':
            the_gate = CNOT(*qid_tuple)
        elif name == 'CY':
            the_gate = CGate(qid_tuple[0], Y(qid_tuple[1]))  # qid_tuple: control target
        elif name == 'CZ':
            the_gate = CGate(qid_tuple[0], Z(qid_tuple[1]))  # qid_tuple: control target
        elif name == 'CCX' or name == 'CCNOT' or name == 'TOFFOLI':
            the_gate = CGate((qid_tuple[0], qid_tuple[1]), X(qid_tuple[2]))

        if the_gate is not None:
            return the_gate

        # U gate, CU gate handled below
        if name.startswith('U') or name.startswith('CU'):
            parameters = params

            if len(parameters) == 1:  # [theta=0, phi=0, lambda]
                parameters.insert(0, 0.0)
                parameters.insert(0, 0.0)
            elif len(parameters) == 2:  # [theta=pi/2, phi, lambda]
                parameters.insert(0, pi/2)
            elif len(parameters) == 3:  # [theta, phi, lambda]
                pass
            else:
                raise SympySimulatorError('U gate must carry 1, 2 or 3 parameters!')

            if name.startswith('U'):
                ugate = UGateGeneric(*qid_tuple)
                u_mat = compute_ugate_matrix(parameters)
                ugate.set_target_matrix(u_matrix=u_mat)
                return ugate

            elif name.startswith('CU'):  # additional treatment for CU1, CU2, CU3
                ugate = UGateGeneric(*qid_tuple)
                u_mat = compute_ugate_matrix(parameters)
                ugate.set_target_matrix(u_matrix=u_mat)
                return CGate(qid_tuple[0], ugate)
        # if the control flow comes here,  alarm!
        raise SympySimulatorError('Not supported')

    # TODO: Remove duplication of _validate between files in statevector_simulator_*.py:
    def _validate(self, qobj):
        """Semantic validations of the qobj which cannot be done via schemas.
        Some of these may later move to backend schemas.

        Args:
            qobj (Qobj): Qobj structure.

        Raises:
            SympySimulatorError: if unsupported operations passed, these are measure and reset
        """
        for circuit in qobj.experiments:
            for operator in circuit.instructions:
                if operator.name in ('measure', 'reset'):
                    raise SympySimulatorError(
                        "In circuit {}: statevector simulator does not support measure or "
                        "reset.".format(circuit.header.name))
