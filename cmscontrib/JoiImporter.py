#!/usr/bin/env python2
# -*- coding: utf-8 -*-

# Programming contest management system
# Copyright © 2010-2012 Giovanni Mascellani <mascellani@poisson.phc.unipi.it>
# Copyright © 2010-2012 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2010-2012 Matteo Boscariol <boscarim@hotmail.com>
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

"""This service load a contest from a tree structure "similar" to the
one used in Japanese IOI repository.

"""

import yaml
import simplejson as json
import os
import codecs
import argparse
import re
import pytz
from datetime import datetime, tzinfo
import calendar
import zipfile

import sqlalchemy.exc

from cms import logger
from cms.db import analyze_all_tables
from cms.db.FileCacher import FileCacher
from cms.db.SQLAlchemyAll import metadata, SessionGen, Manager, \
    Testcase, User, Contest, SubmissionFormatElement, FSObject, \
    Submission, Statement, Attachment

class JoiLoader:
    """Actually look into the directory of the contest, and load all
    data and files.

    """
    def __init__(self, file_cacher, drop, modif, user_number):
        self.drop = drop
        self.modif = modif
        self.user_number = user_number
        self.file_cacher = file_cacher

    def get_params_for_contest(self, conffile):
        """Given the path of a contest, extract the data from its
        contest.yaml file, and create a dictionary with the parameter
        required by Contest.import_from_dict().

        Returns that dictionary and the two pieces of data that must
        be processed with get_params_for_task and
        get_params_for_users.

        path (string): the input directory.

        return (dict): data of the contest.

        """
        path = os.path.realpath(os.path.split(conffile)[0])
        conf = yaml.load(codecs.open(
                conffile,
                "r", "utf-8"))

        logger.info("Loading parameters for contest %s." % conf["name"])

        self.auto_attach = conf.get("auto_attach", False)
        self.use_task_statements = conf.get("use_task_statements", True)

        params = {}
        params["name"] = conf["name"]
        params["description"] = conf["description"]
        params["timezone"] = conf.get("timezone", None)
        params["token_initial"] = conf.get("token_initial", None)
        params["token_max"] = conf.get("token_max", None)
        params["token_total"] = conf.get("token_total", None)
        params["token_min_interval"] = conf.get("token_min_interval", 0)
        params["token_gen_time"] = conf.get("token_gen_time", 0)
        params["token_gen_number"] = conf.get("token_gen_number", 0)

        try:
            timezone = pytz.timezone(params["timezone"])
        except:
            timezone = pytz.utc

        if params["token_gen_time"] is None or \
               params["token_gen_number"] is None:
            params["token_gen_time"] = 1
            params["token_gen_number"] = 0
        if self.modif == 'zero_time':
            params["start"] = 0
            params["stop"] = 0
        elif self.modif == 'test':
            params["start"] = 0
            params["stop"] = 2000000000
        else:
            start_time = timezone.localize(
                    datetime.strptime(conf["start"], "%Y/%m/%d %H:%M:%S"))
            stop_time = timezone.localize(
                    datetime.strptime(conf["stop"], "%Y/%m/%d %H:%M:%S"))
            start_time = start_time.astimezone(pytz.utc)
            stop_time = stop_time.astimezone(pytz.utc)
            params["start"] = calendar.timegm(start_time.timetuple())
            params["stop"] = calendar.timegm(stop_time.timetuple())

        params["max_submission_number"] = \
            conf.get("max_submission_number", None)
        params["max_user_test_number"] = \
            conf.get("max_user_test_number", None)
        params["min_submission_interval"] = \
            conf.get("min_submission_interval", None)
        params["min_user_test_interval"] = \
            conf.get("min_user_test_interval", None)

        logger.info("Contest parameters loaded.")

        params["tasks"] = []
        params["users"] = []
        params["announcements"] = []

        return params, conf["problems"], conf["users"]

    def get_params_for_user(self, user_dict):
        """Given the dictionary of information of a user (extracted
        from contest.yaml), it fills another dictionary with the
        parameters required by User.import_from_dict().

        """
        params = {}
        params["username"] = user_dict["username"]

        logger.info("Loading parameters for user %s." % params['username'])

        if self.modif == 'test':
            params["password"] = 'a'
            params["ip"] = None
        else:
            params["password"] = user_dict["password"]
            params["ip"] = user_dict.get("ip", None)
        name = user_dict.get("name", "")
        surname = user_dict.get("surname", user_dict["username"])
        params["first_name"] = name
        params["last_name"] = surname
        params["hidden"] = "True" == user_dict.get("fake", "False")

        params["timezone"] = None
        params["messages"] = []
        params["questions"] = []
        params["submissions"] = []
        params["user_tests"] = []

        logger.info("User parameters loaded.")

        return params

    def get_params_for_task(self, super_path, task_info, num):
        """Given the path of a task, this function put all needed data
        into FS, and fills the dictionary of parameters required by
        Task.import_from_dict().

        path (string): path of the task.
        num (int): number of the task in the contest task ordering.

        return (dict): info of the task.

        """
        super_path = os.path.realpath(super_path)
        name = task_info["name"]
        dirname = task_info["dir"]
        path = os.path.join(super_path, dirname)
        # conf = yaml.load(codecs.open(
        #     os.path.join(super_path, name + ".yaml"), "r", "utf-8"))

        logger.info("Loading parameters for task %s." % name)

        params = {"name": name}
        params["title"] = task_info["title"]
        if name == params["title"]:
            logger.warning("Short name equals long name (title). "
                           "Please check.")
        params["num"] = num
        params["time_limit"] = task_info.get("time_limit", None)
        params["time_limit"] = float(params["time_limit"]) \
                if params["time_limit"] is not None else None
        params["memory_limit"] = task_info.get("memory_limit", None)
        params["attachments"] = []  # FIXME - Use auxiliary

        if not os.path.exists(path):
            raise Exception("Task directory does not exist.")

        if params.get("auto_attach", self.auto_attach) == True:
            if not os.path.exists(os.path.join(path, "attach")):
                os.mkdir(os.path.join(path, "attach"))
            archive = zipfile.ZipFile(os.path.join(path, "attach", \
                    name+".zip"), "w", zipfile.ZIP_DEFLATED)
            if os.path.exists(os.path.join(path, "sample")):
                for filename in os.listdir(os.path.join(path, "sample")):
                    archive.write(os.path.join(path, "sample", filename), \
                            os.path.join(name, filename))
            if os.path.exists(os.path.join(path, "dist")):
                for filename in os.listdir(os.path.join(path, "dist")):
                    archive.write(os.path.join(path, "dist", filename), \
                            os.path.join(name, filename))
            archive.close()

        if os.path.exists(os.path.join(path, "attach")):
            for filename in os.listdir(os.path.join(path, "attach")):
                attach_digest = self.file_cacher.put_file(
                    path=os.path.join(path, "attach", filename),
                    description="Attachment for task %s" % name)
                params["attachments"].append(Attachment(
                        filename,
                        attach_digest).export_to_dict())

        task_file = None
        if self.use_task_statements == True:
            if os.path.exists(os.path.join(path, "task", "task.pdf")):
                task_file = os.path.join(path, "task", "task.pdf")
            elif os.path.exists(os.path.join(path, "task", dirname+".pdf")):
                task_file = os.path.join(path, "task", dirname+".pdf")
            else:
                raise Exception("Task statement does not exist.")
            params["statements"] = [
                Statement(
                    "",
                    self.file_cacher.put_file(
                        path=task_file,
                        description="Statement for task %s (lang: )" % name),
                    ).export_to_dict()]
        else:
            params["statements"] = []

        params["submission_format"] = [
            SubmissionFormatElement("%s.%%l" % name).export_to_dict()]

        params["primary_statements"] = "[\"\"]"

        # Builds the parameters that depend on the task type
        params["managers"] = []
        infile_param = ""
        outfile_param = ""

        # If there is cms/grader.%l for some language %l, then,
        # presuming that the task type is Batch, we retrieve graders
        # in the form cms/grader.%l
        graders = False
        for lang in Submission.LANGUAGES:
            if os.path.exists(os.path.join(path, "cms", "grader.%s" % (lang))):
                graders = True
                break
        if graders:
            # Read grader for each language
            for lang in Submission.LANGUAGES:
                grader_filename = os.path.join(path, "cms", "grader.%s" %
                                               (lang))
                if os.path.exists(grader_filename):
                    params["managers"].append(Manager(
                        "grader.%s" % (lang),
                        self.file_cacher.put_file(
                                path=grader_filename,
                                description="Grader for task %s and "
                                "language %s" % (name, lang)),
                            ).export_to_dict())
                else:
                    logger.error("Grader for language %s not found " % lang)
            # Read managers with other known file extensions
            for other_filename in os.listdir(os.path.join(path, "cms")):
                if other_filename.endswith('.h') or \
                        other_filename.endswith('lib.pas'):
                    params["managers"].append(Manager(
                        other_filename,
                        self.file_cacher.put_file(
                            path=os.path.join(path, "cms",
                                              other_filename),
                            description="Manager %s for task %s" %
                            (other_filename, name))
                        ).export_to_dict())
            compilation_param = "grader"
        else:
            compilation_param = "alone"

        # If there is cor/correttore, then, presuming that the task
        # type is Batch or OutputOnly, we retrieve the comparator
        if os.path.exists(os.path.join(path, "cms", "checker")):
            params["managers"].append(Manager(
                "checker",
                self.file_cacher.put_file(
                    path=os.path.join(path, "cms", "checker"),
                    description="Manager for task %s" % (name)),
                ).export_to_dict())
            evaluation_parameter = "comparator"
        else:
            evaluation_parameter = "diff"

        testfiles = []
        testfile_meta = {}

        scoretxt_filename = os.path.join(path, 'etc', 'score.txt')
        try:
            with open(scoretxt_filename) as scoretxt_file:
                for testfile_real in os.listdir(os.path.join(path, "in")):
                    testfile = testfile_real.replace(".txt", "")
                    testfiles.append(testfile)
                    testfile_meta[testfile] = [testfile_real, False]
                testfiles.sort()

                testgroups = []

                feedbacks_wc = None
                for line in scoretxt_file:
                    line = line.strip()
                    splitted = line.split(':', 2)

                    if len(splitted) == 1:
                        raise Exception("There should be at least "
                                "one ':' sign.")

                    elif splitted[0].strip()=="Feedback":
                        if feedbacks_wc:
                            raise Exception("There should be only one "
                                    "Feedback line.")
                        feedbacks_wc = splitted[1].split(',')
                    else:
                        group_matched = re.match(
                                r'([0-9a-zA-Z_ ]+)\(\s*(\d+)\s*\)\s*\Z', splitted[0])
                        if not group_matched:
                            raise Exception("malformed score.txt")
                        group_name, group_score = group_matched.groups()
                        files_wc = splitted[1].split(',')
                        testgroup = []
                        for file_wc in files_wc:
                            file_r = re.compile(
                                    file_wc.strip().replace("*", ".*")+r'\Z')
                            for i, testfile in enumerate(testfiles):
                                if file_r.match(testfile):
                                    testgroup.append(i)

                        testgroups.append({'name':group_name, 'files':testgroup, 'score':float(group_score)})

                assert(100 == sum([int(st['score']) for st in testgroups]))
                params["score_type"] = "JoiGroupMin"
                params["score_type_parameters"] = json.dumps(
                        {'testfiles':testfiles, 'testgroups':testgroups})
                if feedbacks_wc:
                    for feedback_wc in feedbacks_wc:
                        feedback_r = re.compile(
                                feedback_wc.strip().replace("*", ".*")+r'\Z')
                        for testfile in testfiles:
                            if feedback_r.match(testfile):
                                testfile_meta[testfile][1] = True
                else:
                    raise Exception("There should be one Feedback line.")

        except IOError:
            raise Exception("score.txt does not exist")

        # OutputOnly (TODO)
        if task_info.get('task_type', "Batch") == 'OutputOnly':
            params["task_type"] = "OutputOnly"
            params["time_limit"] = None
            params["memory_limit"] = None
            params["task_type_parameters"] = '["%s"]' % (evaluation_parameter)
            params["submission_format"] = [
                SubmissionFormatElement("output_%03d.txt" % i).export_to_dict()
                for i in xrange(int(task_info["n_input"]))]

        # Communication2
        elif task_info.get('task_type', "Batch") == 'Communication2':
            params["task_type"] = "Communication2"
            params["task_type_parameters"] = '[]'
            params["submission_format"] = [
                SubmissionFormatElement(f).export_to_dict()
                for f in task_info["submission_format"]]
            params["managers"].append(Manager(
                    "manager",
                    self.file_cacher.put_file(
                        path=os.path.join(path, "cms", "manager"),
                        description="Manager for task %s" % (name)),
                    ).export_to_dict())
            for lang in Submission.LANGUAGES:
                stub_name = os.path.join(path, "cms", "stub.%s" % lang)
                if os.path.exists(stub_name):
                    params["managers"].append(Manager(
                        "stub.%s" % lang,
                        self.file_cacher.put_file(
                            path=stub_name,
                            description="Stub for task %s and language %s" %
                            (name, lang)),
                        ).export_to_dict())
                else:
                    logger.error("Stub for language %s not found." % lang)
            # Read managers with other known file extensions
            for other_filename in os.listdir(os.path.join(path, "cms")):
                if other_filename.endswith('.h') or \
                        other_filename.endswith('lib.pas'):
                    params["managers"].append(Manager(
                        other_filename,
                        self.file_cacher.put_file(
                            path=os.path.join(path, "cms",
                                              other_filename),
                            description="Manager %s for task %s" %
                            (other_filename, name))
                        ).export_to_dict())

        # Communication
        elif task_info.get('task_type', "Batch") == 'Communication':
            params["task_type"] = "Communication"
            params["task_type_parameters"] = '[]'
            params["managers"].append(Manager(
                    "manager",
                    self.file_cacher.put_file(
                        path=os.path.join(path, "cms", "manager"),
                        description="Manager for task %s" % (name)),
                    ).export_to_dict())
            for lang in Submission.LANGUAGES:
                stub_name = os.path.join(path, "cms", "stub.%s" % lang)
                if os.path.exists(stub_name):
                    params["managers"].append(Manager(
                        "stub.%s" % lang,
                        self.file_cacher.put_file(
                            path=stub_name,
                            description="Stub for task %s and language %s" %
                            (name, lang)),
                        ).export_to_dict())
                else:
                    logger.error("Stub for language %s not found." % lang)
            # Read managers with other known file extensions
            for other_filename in os.listdir(os.path.join(path, "cms")):
                if other_filename.endswith('.h') or \
                        other_filename.endswith('lib.pas'):
                    params["managers"].append(Manager(
                        other_filename,
                        self.file_cacher.put_file(
                            path=os.path.join(path, "cms",
                                              other_filename),
                            description="Manager %s for task %s" %
                            (other_filename, name))
                        ).export_to_dict())

        # Otherwise, the task type is Batch
        else:
            params["task_type"] = "Batch"
            params["task_type_parameters"] = \
                '["%s", ["%s", "%s"], "%s"]' % \
                (compilation_param, infile_param, outfile_param,
                 evaluation_parameter)

        params["testcases"] = []
        for num, testfile in enumerate(testfiles):
            testfile_real = testfile_meta[testfile][0]
            _input = os.path.join(path, "in", testfile_real)
            output = os.path.join(path, "out", testfile_real)
            input_digest = self.file_cacher.put_file(
                path=_input,
                description="Input %s for task %s" % (testfile, name))
            output_digest = self.file_cacher.put_file(
                path=output,
                description="Output %s for task %s" % (testfile, name))
            params["testcases"].append(Testcase(
                num=num,
                public=testfile_meta[testfile][1],
                input=input_digest,
                output=output_digest).export_to_dict())
            if params["task_type"] == "OutputOnly":
                params["attachments"].append(Attachment(
                        "input_%03d.txt" % (i),
                        input_digest).export_to_dict())
        params["token_initial"] = task_info.get("token_initial", None)
        params["token_max"] = task_info.get("token_max", None)
        params["token_total"] = task_info.get("token_total", None)
        params["token_min_interval"] = task_info.get("token_min_interval", 0)
        params["token_gen_time"] = task_info.get("token_gen_time", 0)
        params["token_gen_number"] = task_info.get("token_gen_number", 0)

        params["max_submission_number"] = \
            task_info.get("max_submission_number", None)
        params["max_user_test_number"] = \
            task_info.get("max_user_test_number", None)
        params["min_submission_interval"] = \
            task_info.get("min_submission_interval", None)
        params["min_user_test_interval"] = \
            task_info.get("min_user_test_interval", None)

        logger.info("Task parameters loaded.")

        return params

    def import_contest(self, conffile):
        """Import a contest into the system, returning a dictionary
        that can be passed to Contest.import_from_dict().

        """
        path = os.path.realpath(os.path.split(conffile)[0])
        params, tasks, users = self.get_params_for_contest(conffile)
        for i, task_info in enumerate(tasks):
            task = task_info["name"]
            task_params = self.get_params_for_task(path,
                                                   task_info,
                                                   num=i)
            params["tasks"].append(task_params)
        if self.user_number is None:
            for user in users:
                user_params = self.get_params_for_user(user)
                params["users"].append(user_params)
        else:
            logger.info("Generating %s random users." % self.user_number)
            for i in xrange(self.user_number):
                user = User("User %d" % (i),
                            "Last name %d" % (i),
                            "user%03d" % (i))
                if self.modif == 'test':
                    user.password = 'a'
                params["users"].append(user.export_to_dict())

        return params


class JoiImporter:
    """This service load a contest from a tree structure "similar" to
    the one used in Italian IOI repository.

    """
    def __init__(self, drop, modif, conffile, user_number):
        self.drop = drop
        self.modif = modif
        self.conffile = conffile
        self.user_number = user_number

        self.file_cacher = FileCacher()

        self.loader = JoiLoader(self.file_cacher, drop, modif, user_number)

    def run(self):
        """Interface to make the class do its job."""
        self.do_import()

    def do_import(self):
        """Take care of creating the database structure, delegating
        the loading of the contest data and putting them on the
        database.

        """
        logger.info("Creating database structure.")
        if self.drop:
            try:
                with SessionGen() as session:
                    FSObject.delete_all(session)
                    session.commit()
                metadata.drop_all()
            except sqlalchemy.exc.OperationalError as error:
                logger.critical("Unable to access DB.\n%r" % error)
                return False
        try:
            metadata.create_all()
        except sqlalchemy.exc.OperationalError as error:
            logger.critical("Unable to access DB.\n%r" % error)
            return False

        contest = Contest.import_from_dict(
            self.loader.import_contest(self.conffile))

        logger.info("Creating contest on the database.")
        with SessionGen() as session:
            session.add(contest)
            logger.info("Analyzing database.")
            session.commit()
            contest_id = contest.id
            analyze_all_tables(session)

        logger.info("Import finished (new contest id: %s)." % contest_id)

        return True


def main():
    """Parse arguments and launch process.

    """
    parser = argparse.ArgumentParser(
        description="Importer from the Italian repository for CMS.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("-z", "--zero-time", action="store_true",
                       help="set to zero contest start and stop time")
    group.add_argument("-t", "--test", action="store_true",
                       help="setup a contest for testing "
                       "(times: 0, 2*10^9; ips: NULL, passwords: a)")
    parser.add_argument("-d", "--drop", action="store_true",
                        help="drop everything from the database "
                        "before importing")
    parser.add_argument("-n", "--user-number", action="store", type=int,
                        help="put N random users instead of importing them")
    parser.add_argument("import_conffile",
                        help="conffile which describes the contest")

    args = parser.parse_args()

    modif = None
    if args.test:
        modif = 'test'
    elif args.zero_time:
        modif = 'zero_time'

    JoiImporter(drop=args.drop,
                 modif=modif,
                 conffile=args.import_conffile,
                 user_number=args.user_number).run()


if __name__ == "__main__":
    main()
