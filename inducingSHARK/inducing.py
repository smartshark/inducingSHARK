#!/usr/bin/env python

from mongoengine import connect

from pycoshark.mongomodels import Project, VCSSystem, File, Commit, FileAction, Tag, Issue, IssueSystem, Event
from pycoshark.utils import create_mongodb_uri_string


from util.git import CollectGit


import re
import math


def tag_filter(project_name, tags, discard_qualifiers=True, discard_patch=False, discard_fliers=False):
    versions = []

    # qualifiers are expected at the end of the tag and they may have a number attached
    # it is very important for the b to be at the end otherwise beta would already be matched!
    qualifiers = ['rc', 'alpha', 'beta', 'b']

    # separators are expected to divide 2 or more numbers
    separators = ['.', '_', '-']

    for t in tags:

        tag = t.name
        c = Commit.objects.get(id=t.commit_id)

        qualifier = ''
        remove_qualifier = ''
        for q in qualifiers:
            if q in tag.lower():
                tmp = tag.lower().split(q)
                if tmp[-1].isnumeric():
                    qualifier = [q, tmp[-1]]
                    remove_qualifier = ''.join(qualifier)
                    break
                else:
                    qualifier = [q]
                    remove_qualifier = q
                    break

        # if we have a qualifier we remove it before we check for best number seperator
        tmp = tag.lower()
        if qualifier:
            tmp = tmp.split(remove_qualifier)[0]

        # we only want numbers and separators
        version = re.sub(project_name, '', tmp)
        version = re.sub('[a-z]', '', version)

        # the best separator is the one separating the most numbers
        best = -1
        best_sep = None
        for sep in separators:
            current = 0
            for v in version.split(sep):
                v = ''.join(c for c in v if c.isdigit())
                if v.isnumeric():
                    current += 1

            if current > best:
                best = current
                best_sep = sep

        version = version.split(best_sep)
        final_version = []
        for v in version:
            v = ''.join(c for c in v if c.isdigit())
            if v.isnumeric():
                final_version.append(int(v))

        # if we have a version we append it to our list
        if final_version:

            # force semver because we are sorting
            if len(final_version) == 1:
                final_version.append(0)
            if len(final_version) == 2:
                final_version.append(0)

            fversion = {'version': final_version, 'original': tag, 'revision': c.revision_hash}
            if qualifier:
                fversion['qualifier'] = qualifier

            versions.append(fversion)

    # discard fliers
    p_version = [int(v['version'][0]) for v in versions]
    sort = sorted(p_version)
    a = 0.25 * len(sort)
    b = 0.75 * len(sort)
    if a.is_integer():
        a = int(a)  # otherwise could be 6.0
        x_025 = ((sort[a] + sort[a + 1]) / 2)
    else:
        x_025 = sort[math.floor(a) + 1]

    if b.is_integer():
        b = int(b)
        x_075 = ((sort[b] + sort[b + 1]) / 2)
    else:
        x_075 = sort[math.floor(b) + 1]

    iqr = x_075 - x_025
    flyer_lim = 1.5 * iqr

    # then we want to know if we have any fliers
    ret1 = []
    for version in versions:
        major = int(version['version'][0])

        tmp = version.copy()

        # # no fliers in final list
        if major > (x_075 + flyer_lim) or major < (x_025 - flyer_lim):
            tmp['flier'] = True

        ret1.append(tmp)

    ret = []
    for version in ret1:
        if discard_fliers and 'flier' in version.keys():
            continue

        if discard_qualifiers and 'qualifier' in version.keys():
            continue

        ret.append(version)

    # sort remaining
    s = sorted(ret, key=lambda x: (x['version'][0], x['version'][1], x['version'][2]))

    ret = []
    for v in s:
        # only minor, we discard patch releases (3rd in semver, everything after 2nd in other schemas)
        if discard_patch:
            if len(v['version']) > 2:
                del v['version'][2:]

        if v['version'] not in [v2['version'] for v2 in ret]:
            ret.append(v)

    return ret


class InducingMiner:
    """
    Mining of inducing commits with the help of CollectGit and blame
    """

    def __init__(self, logger, database, user, password, host, port, authentication, ssl, project_name, vcs_url, repo_path):
        self._log = logger
        self._repo_path = repo_path
        self._project_name = project_name

        uri = create_mongodb_uri_string(user, password, host, port, authentication, ssl)
        connect(database, host=uri)

        pr = Project.objects.get(name=project_name)
        vcs = VCSSystem.objects.get(project_id=pr.id, url=vcs_url)
        its = IssueSystem.objects.get(project_id=pr.id)

        self._vcs_id = vcs.id
        self._its_id = its.id

    def collect(self):
        self._cg = CollectGit(self._repo_path)
        self._cg.collect()

        self._version_dates = self._collect_version_dates()
        self._clear_inducing()

    def _clear_inducing(self):
        self._log.info('setting all FileAction.induces to []')
        for c in Commit.objects.filter(vcs_system_id=self._vcs_id).only('id'):
            for fa in FileAction.objects.filter(commit_id=c.id):
                fa.induces = []  # this deletes everything, also previous runs with a different label
                fa.save()

    def _find_boundary_date(self, issue_ids, version_dates, affected_versions):
        """Find suspect boundary date.

        latest issue information but earliest date in commit (between created_at and affected versions)

        - latest creation date of linked bugs
        - earliest affected version
        """

        issue_dates = []
        affected_version_dates = []
        for issue_id in issue_ids:
            issue = Issue.objects.get(id=issue_id)

            if not issue.created_at:
                self._log.warn('no reporting date for {} id({}), ignoring it'.format(issue.external_id, issue.id))
                continue

            for av in issue.affects_versions:
                avt = tuple(av.split('.'))
                if avt in version_dates.keys():

                    for version_date in version_dates[avt]:
                        if version_date not in affected_version_dates:
                            affected_version_dates.append(version_date)
                else:
                    self._log.warn('affected version {} not found'.format(avt))

            issue_dates.append(issue.created_at)

        # find latest bug report
        suspect_boundary_date = max(issue_dates)

        # latest bug report
        self._log.debug('latest bug report date is {} of {}'.format(suspect_boundary_date, issue_dates))

        # return earliest affected version, only if we want
        if affected_versions and affected_version_dates:
            min_affected_date = min(affected_version_dates)
            self._log.debug('affected versions earliest date is {} while max bug report date is {}'.format(min_affected_date, suspect_boundary_date))
            suspect_boundary_date = min(min_affected_date, suspect_boundary_date)

        return suspect_boundary_date

    def _collect_version_dates(self):
        """Match affected versions from the ITS to tag names from the VCS.

        3.0.0 from ITS matches 3.0.0 from VCS
        3.0 from ITS matches all of 3.0.X from VCS
        """
        tags = tag_filter(self._project_name, Tag.objects.filter(vcs_system_id=self._vcs_id), discard_qualifiers=True, discard_patch=False, discard_fliers=False)

        # collect tags and their version and date used in this VCS system
        tag_versions = {}
        for t in tags:
            c = Commit.objects.get(vcs_system_id=self._vcs_id, revision_hash=t['revision'])
            tag_versions[tuple([str(tv) for tv in t['version']])] = c.committer_date

        # collect affected versions used in this ITS
        affected_versions = set()
        for i in Issue.objects.filter(issue_system_id=self._its_id):
            for av in i.affects_versions:
                affected_versions.add(tuple(av.split('.')))

        # map affected versions to possible dates
        version_dates = {}
        for av in affected_versions:
            for tv, dt in tag_versions.items():
                startswith = all(tv1 == av1 for tv1, av1 in zip(tv, av))  # check if evey item of the tuple matches
                if not startswith:
                    continue
                if av not in version_dates.keys():
                    version_dates[av] = []

                version_dates[av].append(dt)
        return version_dates

    def write_bug_inducing(self, label='validated_bugfix', inducing_strategy='code_only', java_only=True, affected_versions=False, name=None):
        """Write bug inducing information into FileAction.

        1. get all commits that are bug-fixing
        2. run blame for all files for all deleted lines in bug-fixing commits to find bug-inducing file actions and commits
        3. save to mongo_db
        """
        params = {
            'vcs_system_id': self._vcs_id,
            'labels__{}'.format(label): True,
            'parents__1__exists': False,
        }

        if label == 'validated_bugfix':
            params['fixed_issue_ids__0__exists'] = True
        elif label == 'adjustedszz_bugfix':
            params['szz_issue_ids__0__exists'] = True
        else:
            raise Exception('unknown label')

        all_changes = {}
        for bugfix_commit in Commit.objects.filter(**params).only('revision_hash', 'id', 'fixed_issue_ids', 'szz_issue_ids', 'committer_date').timeout(False):

            # only modified files
            for fa in FileAction.objects.filter(commit_id=bugfix_commit.id, mode='M'):
                f = File.objects.get(id=fa.file_id)

                # only java files
                if java_only and not f.path.lower().endswith('.java'):
                    continue

                if label == 'validated_bugfix':
                    suspect_boundary_date = self._find_boundary_date(bugfix_commit.fixed_issue_ids, self._version_dates, affected_versions)
                elif label == 'adjustedszz_bugfix':
                    suspect_boundary_date = self._find_boundary_date(bugfix_commit.szz_issue_ids, self._version_dates, affected_versions)
                else:
                    raise Exception('unknown label')

                # find bug inducing commits, add to our list for this commit and file
                for blame_commit, original_file in self._cg.blame(bugfix_commit.revision_hash, f.path, inducing_strategy):
                    blame_c = Commit.objects.get(vcs_system_id=self._vcs_id, revision_hash=blame_commit)

                    # every commit before our suspect boundary date is counted towards inducing
                    if blame_c.committer_date < suspect_boundary_date:
                        szz_type = 'inducing'

                    # every commit behind our boundary date is counted towards suspects
                    elif blame_c.committer_date >= suspect_boundary_date:
                        szz_type = 'suspect'

                        # if the suspect commit is also a bug-fix it is a partial fix
                        if label in blame_c.labels.keys() and blame_c.labels[label] is True:
                            szz_type = 'partial_fix'

                    self._log.debug('blame commit date {} against boundary date {}, szz_type {}'.format(blame_c.committer_date, suspect_boundary_date, szz_type))
                    for blame_fa in FileAction.objects.filter(commit_id=blame_c.id):
                        blame_f = File.objects.get(id=blame_fa.file_id)

                        if blame_f.path == original_file:
                            key = str(fa.id) + '_' + str(blame_fa.id)

                            if key not in all_changes.keys():
                                all_changes[key] = {'change_file_action_id': fa.id, 'inducing_file_action': blame_fa.id, 'label': label, 'szz_type': szz_type, 'inducing_strategy': inducing_strategy}

        # second run differenciate between hard and weak suspects
        new_types = {}
        self._log.debug('starting second pass for distinguish hard and weak suspects')
        for change, values in all_changes.items():

            # every suspect starts as hard_suspect
            szz_type = 'hard_suspect'
            if values['szz_type'] == 'suspect':

                # is there a fix for this change which is not a suspect (which means it has to be a partial-fix or inducing)
                # we set this type to weak_suspect
                for change2, values2 in all_changes.items():

                    # skip equal
                    if change == change2:
                        continue

                    if values2['inducing_file_action'] == values['inducing_file_action'] and values2['szz_type'] != 'suspect':
                        szz_type = 'weak_suspect'
                        self._log.debug('found another inducing change for this inducing change which is not a suspect, we set szz_type to weak_suspect')
                new_types[change] = szz_type

        # write results
        self._log.debug('writing results')
        for change, values in all_changes.items():
            fa = FileAction.objects.get(id=values['inducing_file_action'])

            szz_type = values['szz_type']
            if szz_type == 'suspect':
                szz_type = new_types[change]

            to_write = {'change_file_action_id': values['change_file_action_id'],
                        'szz_type': szz_type,
                        # these values are defined by the name
                        # 'label': values['label'],
                        # 'inducing_strategy': inducing_strategy,
                        # 'java_only': java_only,
                        # 'affected_versions': affected_versions,
                        'label': name}

            self._log.debug(to_write)
            # we clear everything with this label beforehand because we may re-run this plugin with a different label or strategy
            # new_list = []
            # for d in fa.induces:
            #     if d['label'] != label or d['inducing_strategy'] != inducing_strategy or d['java_only'] != java_only:  # keep values not matching our stuff
            #         new_list.append(d)

            # fa.induces = new_list
            # fa.induces = []  # this deletes everything, also previous runs with a different label

            if to_write not in fa.induces:
                fa.induces.append(to_write)
            fa.save()
