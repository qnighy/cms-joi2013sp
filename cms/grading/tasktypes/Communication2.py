#!/usr/bin/env python2
# -*- coding: utf-8 -*-

# Programming contest management system
# Copyright © 2010-2012 Giovanni Mascellani <mascellani@poisson.phc.unipi.it>
# Copyright © 2010-2012 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2010-2012 Matteo Boscariol <boscarim@hotmail.com>
# Copyright © 2012 Luca Wehrstedt <luca.wehrstedt@gmail.com>
# Copyright © 2012 Masaki Hara <ackie.h.gmai@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import os
import tempfile
import shutil

from cms import config, logger
from cms.grading.Sandbox import wait_without_std
from cms.grading import get_compilation_command, compilation_step, \
    human_evaluation_message, is_evaluation_passed, \
    extract_outcome_and_text, evaluation_step_before_run, \
    evaluation_step_after_run
from cms.grading.TaskType import TaskType, \
     create_sandbox, delete_sandbox
from cms.db.SQLAlchemyAll import Submission, Executable

HEADERS_MAP = {
    "c": "h",
    "cpp": "h",
    "pas": "lib.pas",
    }

class Communication2(TaskType):
    """Task type class for tasks that requires:

    - a *manager* that reads the input file, work out the perfect
      solution on its own, and communicate the input (maybe with some
      modifications) on its standard output; it then reads the
      response of the user's solution from the standard input and
      write the outcome;

    - a *stub* that compiles with the user's source, reads from
      standard input what the manager says, and write back the user's
      solution to stdout.

    """
    ALLOW_PARTIAL_SUBMISSION = False

    name = "Communication2"

    def get_compilation_commands(self, submission_format):
        """See TaskType.get_compilation_commands."""
        res = dict()
        for language in Submission.LANGUAGES:
            source_filenames = []
            source_filenames.append("stub.%s" % language)
            for filename in submission_format:
                source_filename = filename.replace("%l", language)
                source_filenames.append(source_filename)
            executable_filename = "user_program"
            command = " ".join(get_compilation_command(language,
                                                       source_filenames,
                                                       executable_filename))
            res[language] = [command]
        return res

    def get_user_managers(self, submission_format):
        """See TaskType.get_user_managers."""
        return ["stub.%l"]

    def get_auto_managers(self):
        """See TaskType.get_auto_managers."""
        return ["manager"]

    def compile(self):
        """See TaskType.compile."""
        # Detect the submission's language. The checks about the
        # formal correctedness of the submission are done in CWS,
        # before accepting it.
        language = self.job.language
        header = HEADERS_MAP[language]

        # TODO: here we are sure that submission.files are the same as
        # task.submission_format. The following check shouldn't be
        # here, but in the definition of the task, since this actually
        # checks that task's task type and submission format agree.
        if len(self.job.files) != 2:
            self.job.success = True
            self.job.compilation_success = False
            self.job.text = "Invalid files in submission"
            logger.error("Submission contains %d files, expecting 2" %
                         len(self.job.files))
            return True

        # compilation sandbox
        sandbox = create_sandbox(self)
        self.job.sandboxes.append(sandbox.path)

        # Prepare the source files in the sandbox
        files_to_get = {}
        source_filenames = []
        # Stub.
        stub_filename = "stub.%s" % language
        source_filenames.append(stub_filename)
        files_to_get[stub_filename] = \
                self.job.managers[stub_filename].digest
        # User's submission.
        for filename, _file in self.job.files.iteritems():
            source_filename = filename.replace("%l", language)
            source_filenames.append(source_filename)
            files_to_get[source_filename] = _file.digest
        # Headers.
        for manager_filename, manager in self.job.managers.items():
            if manager_filename.endswith("." + HEADERS_MAP[language]):
                source_filenames.append(manager_filename)
                files_to_get[manager_filename] = manager.digest
        for filename, digest in files_to_get.iteritems():
            sandbox.create_file_from_storage(filename, digest)

        # Prepare the compilation command
        executable_filename = "user_program"
        command = get_compilation_command(language,
                                          source_filenames,
                                          executable_filename)

        # Run the compilation
        operation_success, compilation_success, text, plus = \
            compilation_step(sandbox, command)

        # Retrieve the compiled executables
        self.job.success = operation_success
        self.job.compilation_success = compilation_success
        self.job.plus = plus
        self.job.text = text
        if operation_success and compilation_success:
            digest = sandbox.get_file_to_storage(
                executable_filename,
                "Executable %s for %s" %
                (executable_filename, self.job.info))
            self.job.executables[executable_filename] = \
                Executable(executable_filename, digest)

        # Cleanup
        delete_sandbox(sandbox)

    def evaluate_testcase(self, test_number):
        """See TaskType.evaluate_testcase."""
        # Create sandboxes and FIFOs
        sandbox_mgr = create_sandbox(self)
        sandbox_user1 = create_sandbox(self)
        sandbox_user2 = create_sandbox(self)
        fifo_dir1 = tempfile.mkdtemp(dir=config.temp_dir)
        fifo_dir2 = tempfile.mkdtemp(dir=config.temp_dir)
        fifo_in1 = os.path.join(fifo_dir1, "in1")
        fifo_out1 = os.path.join(fifo_dir1, "out1")
        fifo_in2 = os.path.join(fifo_dir2, "in2")
        fifo_out2 = os.path.join(fifo_dir2, "out2")
        os.mkfifo(fifo_in1)
        os.mkfifo(fifo_out1)
        os.mkfifo(fifo_in2)
        os.mkfifo(fifo_out2)
        os.chmod(fifo_dir1, 0o755)
        os.chmod(fifo_in1, 0o666)
        os.chmod(fifo_out1, 0o666)
        os.chmod(fifo_dir2, 0o755)
        os.chmod(fifo_in2, 0o666)
        os.chmod(fifo_out2, 0o666)

        # First step: we start the manager.
        manager_filename = "manager"
        manager_command = ["./%s" % manager_filename, fifo_in1, fifo_out1, fifo_in2, fifo_out2]
        manager_executables_to_get = {
            manager_filename:
            self.job.managers[manager_filename].digest
            }
        manager_files_to_get = {
            "input.txt": self.job.testcases[test_number].input
            }
        manager_allow_dirs = [fifo_dir1, fifo_dir2]
        for filename, digest in manager_executables_to_get.iteritems():
            sandbox_mgr.create_file_from_storage(
                filename, digest, executable=True)
        for filename, digest in manager_files_to_get.iteritems():
            sandbox_mgr.create_file_from_storage(filename, digest)
        manager = evaluation_step_before_run(
            sandbox_mgr,
            manager_command,
            self.job.time_limit,
            0,
            allow_dirs=manager_allow_dirs,
            stdin_redirect="input.txt")

        # Second step: user program 1
        user1_executable_filename = "user_program"
        user1_command = ["./%s" % user1_executable_filename, "0", fifo_out1, fifo_in1]
        user1_executables_to_get = {
            user1_executable_filename:
            self.job.executables[user1_executable_filename].digest
            }
        for filename, digest in user1_executables_to_get.iteritems():
            sandbox_user1.create_file_from_storage(
                filename, digest, executable=True)
        user1_allow_dirs = [fifo_dir1]
        user1_process = evaluation_step_before_run(
            sandbox_user1,
            user1_command,
            self.job.time_limit,
            self.job.memory_limit,
            allow_dirs=user1_allow_dirs)

        # Second step: user program 2
        user2_executable_filename = "user_program"
        user2_command = ["./%s" % user2_executable_filename, "1", fifo_out2, fifo_in2]
        user2_executables_to_get = {
            user2_executable_filename:
            self.job.executables[user2_executable_filename].digest
            }
        for filename, digest in user2_executables_to_get.iteritems():
            sandbox_user2.create_file_from_storage(
                filename, digest, executable=True)
        user2_allow_dirs = [fifo_dir2]
        user2_process = evaluation_step_before_run(
            sandbox_user2,
            user2_command,
            self.job.time_limit,
            self.job.memory_limit,
            allow_dirs=user2_allow_dirs)

        # Consume output.
        wait_without_std([user1_process, user2_process, manager])
        # TODO: check exit codes with translate_box_exitcode.

        success_user1, plus_user1 = \
            evaluation_step_after_run(sandbox_user1)
        success_user2, plus_user2 = \
            evaluation_step_after_run(sandbox_user2)
        success_mgr, plus_mgr = \
            evaluation_step_after_run(sandbox_mgr)

        self.job.evaluations[test_number] = \
            {'sandboxes': [sandbox_user1.path,
                           sandbox_user2.path,
                           sandbox_mgr.path],
             'plus': plus_user2}
        evaluation = self.job.evaluations[test_number]

        # If at least one evaluation had problems, we report the
        # problems.
        if not success_user1 or not success_user2 or not success_mgr:
            success, outcome, text = False, None, None
        # If the user sandbox detected some problem (timeout, ...),
        # the outcome is 0.0 and the text describes that problem.
        elif not is_evaluation_passed(plus_user1):
            success = True
            outcome, text = 0.0, human_evaluation_message(plus_user1)
        elif not is_evaluation_passed(plus_user2):
            success = True
            outcome, text = 0.0, human_evaluation_message(plus_user2)
        # Otherwise, we use the manager to obtain the outcome.
        else:
            success = True
            outcome, text = extract_outcome_and_text(sandbox_mgr)

        # If asked so, save the output file, provided that it exists
        if self.job.get_output:
            if sandbox_mgr.file_exists("output.txt"):
                evaluation['output'] = sandbox_mgr.get_file_to_storage(
                    "output.txt",
                    "Output file for testcase %d in job %s" %
                    (test_number, self.job.info))
            else:
                evaluation['output'] = None

        # Whatever happened, we conclude.
        evaluation['success'] = success
        evaluation['outcome'] = str(outcome) if outcome is not None else None
        evaluation['text'] = text
        delete_sandbox(sandbox_mgr)
        delete_sandbox(sandbox_user1)
        delete_sandbox(sandbox_user2)
        shutil.rmtree(fifo_dir1)
        shutil.rmtree(fifo_dir2)
        return success
