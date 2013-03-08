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

import simplejson as json

from cms.grading.ScoreType import ScoreTypeAlone


class JoiGroupMin(ScoreTypeAlone):
    TEMPLATE = """\
{% from cms.server import format_size %}
{% for gr in details %}
    {% if "score" in gr and "max_score" in gr %}
        {% if gr["score"] >= gr["max_score"] %}
<div class="subtask correct">
        {% elif gr["score"] <= 0.0 %}
<div class="subtask notcorrect">
        {% else %}
<div class="subtask partiallycorrect">
        {% end %}
    {% else %}
<div class="subtask undefined">
    {% end %}
    <div class="subtask-head">
        <span class="title">
            {{ gr["name"] }}
        </span>
    {% if "score" in gr and "max_score" in gr %}
        <span class="score">
            {{ '%g' % round(gr["score"], 2) }} / {{ "%g" % round(gr["max_score"], 2) }}
        </span>
    {% else %}
        <span class="score">
            {{ _("N/A") }}
        </span>
    {% end %}
    </div>
    <div class="subtask-body">
        <table class="testcase-list">
            <thead>
                <tr>
                    <th>{{ _("Outcome") }}</th>
                    <th>{{ _("Details") }}</th>
                    <th>{{ _("Time") }}</th>
                    <th>{{ _("Memory") }}</th>
                    <th>{{ _("Testcase") }}</th>
                </tr>
            </thead>
            <tbody>
    {% for tc in gr["testcases"] %}
        {% if "outcome" in tc and "text" in tc %}
            {% if tc["outcome"] == "Correct" %}
                <tr class="correct">
            {% elif tc["outcome"] == "Not correct" %}
                <tr class="notcorrect">
            {% else %}
                <tr class="partiallycorrect">
            {% end %}
                    <td>{{ tc["outcome"] }}</td>
                    <td>{{ tc["text"] }}</td>
                    <td>
            {% if "time" in tc and tc["time"] is not None %}
                        {{ "%(seconds)0.3f s" % {'seconds': tc["time"]} }}
            {% else %}
                        {{ _("N/A") }}
            {% end %}
                    </td>
                    <td>
            {% if "memory" in tc and tc["memory"] is not None %}
                        {{ format_size(tc["memory"]) }}
            {% else %}
                        {{ _("N/A") }}
            {% end %}
                    </td>
                    <td>{{ tc["name"] }}</td>
        {% else %}
                <tr class="undefined">
                    <td colspan="4">
                        {{ _("N/A") }}
                    </td>
                </tr>
        {% end %}
    {% end %}
            </tbody>
        </table>
    </div>
</div>
{% end %}"""

    def max_scores(self):
        """Compute the maximum score of a submission.

        returns (float, float): maximum score overall and public.

        """
        indices = sorted(self.public_testcases.keys())
        public_score = 0.0
        score = 0.0
        for group in self.parameters['testgroups']:
            score += group['score']
            if all(self.public_testcases[indices[i]]
                    for i in group['files']):
                public_score += group['score']
        return score, public_score

    def compute_score(self, submission_id):
        """Compute the score of a submission.

        submission_id (int): the submission to evaluate.
        returns (float): the score

        """
        if not self.pool[submission_id]["evaluated"]:
            return 0.0, "[]", 0.0, "[]", ["%lg" % 0.0 for _ in self.parameters['testgroups']]

        indices = sorted(self.public_testcases.keys())
        evaluations = self.pool[submission_id]["evaluations"]
        subtasks = []
        public_subtasks = []
        ranking_details = []

        for group in [None] + self.parameters['testgroups']:
            group_indices = indices
            if group is not None:
                group_indices = []
                for i in group['files']:
                    group_indices.append(indices[i])

            gr_score, gr_public = 0.0, True

            if group is not None:
                gr_score = min(float(evaluations[idx]["outcome"])
                                        for idx in group_indices) * group['score']
                gr_public = all(self.public_testcases[idx]
                                        for idx in group_indices)

            gr_outcomes = dict((
                idx,
                self.get_public_outcome(
                    float(evaluations[idx]["outcome"]))
                ) for idx in group_indices)

            testcases = []
            public_testcases = []

            for idx in group_indices:
                testcases.append({
                    "name": self.parameters['testfiles'][idx],
                    "outcome": gr_outcomes[idx],
                    "text": evaluations[idx]["text"],
                    "time": evaluations[idx]["time"],
                    "memory": evaluations[idx]["memory"],
                    })
                if self.public_testcases[idx]:
                    public_testcases.append(testcases[-1])
                # else:
                #     public_testcases.append({"name": testcases[-1]["name"]})

            if group is not None:
                subtasks.append({
                    "name": group['name'],
                    "score": gr_score,
                    "max_score": group['score'],
                    "testcases": testcases,
                    })
            else:
                subtasks.append({
                    "name": "All Testcases", # TODO: _()
                    "testcases": testcases,
                    })

            if gr_public:
                if group is not None:
                    public_subtasks.append({
                        "name": group['name'],
                        "score": gr_score,
                        "max_score": group['score'],
                        "testcases": public_testcases,
                        })
                else:
                    public_subtasks.append({
                        "name": "All Testcases", # TODO: _()
                        "testcases": public_testcases,
                        })

            ranking_details.append("%g" % round(gr_score, 2))

        score = sum(st["score"] for st in subtasks
                                        if "score" in st)
        public_score = sum(st["score"] for st in public_subtasks
                                       if "score" in st)

        return score, json.dumps(subtasks), \
               public_score, json.dumps(public_subtasks), \
               ranking_details

    def get_public_outcome(self, outcome):
        """See ScoreTypeGroup."""
        if outcome <= 0.0:
            return "Not correct"
        elif outcome >= 1.0:
            return "Correct"
        else:
            return "Partially correct"
