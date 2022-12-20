import os
from collections import defaultdict
from html.entities import name2codepoint
from dateutil.parser import parse
from datetime import datetime
import re
import requests

from utils import fetch_labels_mapping, fetch_allowed_labels, fetch_people_mapping, fetch_jira_user_mapping, get_github_search_url


media_cache = os.getenv('JIRA_MIGRATION_MEDIA_CACHE')

def jira_attachement(m: re.Match[str]):
    if not media_cache: return m[0]

    url = media_cache + m[1]
    test_url = url + ('&' if '?' in m[1] else '?') + 'check=true'
    response = requests.get(test_url) # cache it
    print('Cache media:', response.status_code)
    return url


class Project:

    def __init__(self, name, doneStatusCategoryId, jiraBaseUrl):
        self.name = name
        self.doneStatusCategoryId = doneStatusCategoryId
        self.jiraBaseUrl = jiraBaseUrl
        self._project = {'Milestones': defaultdict(int), 'Components': defaultdict(
            int), 'Labels': defaultdict(int), 'Types': defaultdict(int), 'Issues': []}

        self.labels_mapping = fetch_labels_mapping()
        self.approved_labels = fetch_allowed_labels()
        self.people_mapping = fetch_people_mapping()
        self.jira_user_mapping = fetch_jira_user_mapping()

    def get_milestones(self):
        return self._project['Milestones']

    def get_components(self):
        return self._project['Components']

    def get_issues(self):
        return self._project['Issues']

    def get_types(self):
        return self._project['Types']

    def get_all_labels(self):
        merge = self._project['Components'].copy()
        merge.update(self._project['Labels'])
        merge.update(self._project['Types'])
        merge.update({'jira': 0})
        return merge

    def get_labels(self):
        merge = self._project['Labels'].copy()
        merge.update({'jira': 0})
        return merge

    def add_item(self, item):
        itemProject = self._projectFor(item)
        if itemProject != self.name:
            print('Skipping item ' + item.key.text + ' for project ' +
                  itemProject + ' current project: ' + self.name)
            return

        self._append_item_to_project(item)

        self._add_milestone(item)

        self._add_labels(item)

        self._add_subtasks(item)

        self._add_parenttask(item)

        self._add_comments(item)

        self._add_relationships(item)

    def prettify(self):
        def hist(h):
            for key in h.keys():
                print(('%30s (%5d): ' + h[key] * '#') % (key, h[key]))
            print

        print(self.name + ':\n  Milestones:')
        hist(self._project['Milestones'])
        print('  Types:')
        hist(self._project['Types'])
        print('  Components:')
        hist(self._project['Components'])
        print('  Labels:')
        hist(self._project['Labels'])
        print
        print('Total Issues to Import: %d' % len(self._project['Issues']))

    def _projectFor(self, item):
        try:
            result = item.project.get('key')
        except AttributeError:
            result = item.key.text.split('-')[0]
        return result

    def _append_item_to_project(self, item):
        closed = str(item.statusCategory.get('id')) == self.doneStatusCategoryId
        closed_at = ''
        if closed:
            try:
                closed_at = self._convert_to_iso(item.resolved.text)
            except AttributeError:
                pass

        # TODO: ensure item.assignee/reporter.get('username') to avoid "JENKINSUSER12345"
        # TODO: fixit in gh issues
        # check if issue description is missing or empty and set a default
        if not hasattr(item, 'description') or not item.description:
            item.description = 'No Description'
        body = self._htmlentitydecode(item.description.text)

        # metadata: original author & link
        assignee = None
        status = ''

        body = body + '\n\n---\n<details><summary><i>Originally reported by <a title="' + str(item.reporter) + '" href="' + self.jiraBaseUrl + '/secure/ViewProfile.jspa?accountid=' + item.reporter.get('accountid') + '">' + str(item.reporter) + '</a>, imported from: <a href="' + self.jiraBaseUrl + '/browse/' + item.key.text + '" target="_blank">' + item.title.text[item.title.text.index("]") + 2:len(item.title.text)] + '</a></i></summary>'
        # metadata: assignee
        body = body + '\n<i><ul>'
        if item.assignee != 'Unassigned':
            assignee = str(item.assignee)
            body = body + '\n<li><b>assignee</b>: <a title="' + str(item.assignee) + '" href="' + self.jiraBaseUrl + '/secure/ViewProfile.jspa?accountid=' + item.assignee.get('accountid') + '">' + str(item.assignee) + '</a>'
        try:
            body = body + '\n<li><b>status</b>: ' + item.status
            status = item.status.text.lower()
        except AttributeError:
            pass
        try:
            body = body + '\n<li><b>priority</b>: ' + item.priority
        except AttributeError:
            pass
        try:
            body = body + '\n<li><b>resolution</b>: ' + item.resolution
        except AttributeError:
            pass
        try:
            body = body + '\n<li><b>resolved</b>: ' + self._convert_to_iso(item.resolved.text)
        except AttributeError:
            pass
        epic_name = self._get_epic(item)
        if epic_name:
            url = get_github_search_url(epic_name)
            body = body + f'\n<li><b>epic</b>: <a href="{url}">{epic_name}</a></i>'
        body = body + '\n<li><b>imported</b>: ' + datetime.today().strftime('%Y-%m-%d')
        body = body + '\n</ul></i>\n</details>'

        # retrieve jira components and labels as github labels
        labels = []

        if status == 'duplicate':
            labels.append('duplicate')
        elif status in ('not a bug', 'not doing'):
            labels.append('wontfix')

        # set a default component if empty or missing
        if not hasattr(item, 'component') or not item.component:
            item.component = 'miscellaneous'
        elif os.getenv('JIRA_MIGRATION_INCLUDE_COMPONENT_IN_LABELS', 'true') == 'true':
            for component in item.component:
                labels.append('jira-component:' + component.text.lower())
                labels.append(component.text.lower())

        labels.append(self._jira_type_mapping(item.type.text.lower()))

        milestone_name = None
        # get the last release label
        for label in item.labels.findall('label'):
            converted_label = label.text.strip().lower()
            if converted_label.startswith('facetalk-'):
                milestone_name = converted_label

        if milestone_name:
            self._project['Milestones'][milestone_name] += 1

        labels.append('jira')

        unique_labels = list(set(labels))

        self._project['Issues'].append({'title': item.title.text,
                                        'key': item.key.text,
                                        'body': body,
                                        'created_at': self._convert_to_iso(item.created.text),
                                        'closed_at': closed_at,
                                        'updated_at': self._convert_to_iso(item.updated.text),
                                        'assignee': self.people_mapping.get(assignee),
                                        'milestone_name': milestone_name,
                                        'closed': closed,
                                        'labels': unique_labels,
                                        'comments': [],
                                        })
        if not self._project['Issues'][-1]['closed_at']:
            del self._project['Issues'][-1]['closed_at']

    def _jira_type_mapping(self, issue_type):
        return issue_type
        # if issue_type == 'bug':
        #     return 'bug'
        # if issue_type == 'improvement':
        #     return 'rfe'
        # if issue_type == 'new feature':
        #     return 'rfe'
        # if issue_type == 'task':
        #     return 'rfe'
        # if issue_type == 'story':
        #     return 'rfe'
        # if issue_type == 'patch':
        #     return 'rfe'
        # if issue_type == 'epic':
        #     return 'epic'

    def _convert_to_iso(self, timestamp):
        dt = parse(timestamp)
        return dt.isoformat()

    def _get_epic(self, item):
        try:
            customfield = item.customfields.find('customfield[@key="com.pyxis.greenhopper.jira:gh-epic-link"]')
            epic_name = re.sub(r'[^\w-]+', ' ', customfield.customfieldvalues.customfieldvalue.text).strip()
            if len(epic_name) < 50:
                return epic_name

            main_name = epic_name.find(' - ')
            if main_name > 0:
                epic_name = epic_name[:main_name]
                return epic_name

            words = epic_name.split(' ')
            while len(epic_name) > 40:
                words.pop()
                epic_name = ' '.join(words)
            return epic_name
        except AttributeError:
            return None

    def _add_milestone(self, item):
        try:
            milestone = item.fixVersion.text.strip()
            self._project['Milestones'][milestone] += 1
            # this prop will be deleted later:
            self._project['Issues'][-1]['milestone_name'] = milestone
        except AttributeError:
            pass

    def _add_labels(self, item):
        issue = self._project['Issues'][-1]

        # turn epic into label
        epic_name = self._get_epic(item)
        if epic_name:
            self._project['Labels'][epic_name] += 1
            issue['labels'].append(epic_name)

        try:
            self._project['Components'][item.component.text] += 1
            tmp_l = item.component.text.strip().lower()
            issue['labels'].append(tmp_l)
        except AttributeError:
            pass

        try:
            for label in item.labels.label:
                if label.startswith('facetalk-'): continue
                self._project['Labels'][label.text] += 1
                tmp_l = label.text.strip().lower()
                issue['labels'].append(tmp_l)
        except AttributeError:
            pass

        # turn customfield_10932 (flagged) into label
        try:
            customfield = item.customfields.find('customfield[@id="customfield_10932"]')
            flag = customfield.customfieldvalues.customfieldvalue.text.strip().lower()
            if flag:
                self._project['Labels'][flag] += 1
                issue['labels'].append(flag)
        except AttributeError:
            pass

        try:
            self._project['Types'][item.type.text] += 1
            tmp_l = item.type.text.strip().lower()
            issue['labels'].append(tmp_l)
        except AttributeError:
            pass

    def _add_subtasks(self, item):
        try:
            subtaskList = ''
            for subtask in item.subtasks.subtask:
                subtaskList = subtaskList + '- ' + subtask + '\n'
            if subtaskList != '':
                print('-> subtaskList: ' + subtaskList)
                self._project['Issues'][-1]['comments'].append(
                    {"created_at": self._convert_to_iso(item.created.text),
                     "body": 'Subtasks:\n\n' + subtaskList})
        except AttributeError:
            pass

    def _add_parenttask(self, item):
        try:
            parentTask = item.parent.text
            if parentTask != '':
                print('-> parentTask: ' + parentTask)
                self._project['Issues'][-1]['comments'].append(
                    {"created_at": self._convert_to_iso(item.created.text),
                     "body": 'Subtask of parent task ' + parentTask})
        except AttributeError:
            pass

    def _add_comments(self, item):
        try:
            for comment in item.comments.comment:
                self._project['Issues'][-1]['comments'].append(
                    {"created_at": self._convert_to_iso(comment.get('created')),
                     "body": '<i><a href="' + self.jiraBaseUrl + '/secure/ViewProfile.jspa?accountid=' + comment.get('author') + '">' + self.jira_user_mapping.get(comment.get('author'), comment.get('author')) + '</a>:</i>\n' + self._htmlentitydecode(comment.text)
                     })
        except AttributeError:
            pass

    def _add_relationships(self, item):
        issue = self._project['Issues'][-1]
        try:
            for issuelinktype in item.issuelinks.issuelinktype:
                for outwardlink in issuelinktype.outwardlinks:
                    tmp_outward = outwardlink.get("description").replace(' ', '-')
                    if tmp_outward not in issue:
                        issue[tmp_outward] = []
                    for issuelink in outwardlink.issuelink:
                        for issuekey in issuelink.issuekey:
                            issue[tmp_outward].append(issuekey.text)
        except AttributeError:
            pass
        except KeyError:
            print('1. KeyError at ' + item.key.text)
        try:
            for issuelinktype in item.issuelinks.issuelinktype:
                for inwardlink in issuelinktype.inwardlinks:
                    tmp_inward = inwardlink.get("description").replace(' ', '-')
                    if tmp_inward not in issue:
                        issue[tmp_inward] = []
                    for issuelink in inwardlink.issuelink:
                        for issuekey in issuelink.issuekey:
                            issue[tmp_inward].append(issuekey.text)
        except AttributeError:
            pass
        except KeyError:
            print('2. KeyError at ' + item.key.text)

        # maintain "key" order
        extra_comments = {
            'customfield_10940': None, # Implementation Strategy
            'customfield_10504': None, # Acceptance Criteria
            'customfield_10933': None, # Test Results
        }
        for customfield in item.customfields.findall('customfield'):
            try:
                customfieldvalue = customfield.customfieldvalues.customfieldvalue.text.strip()
            except:
                continue

            if customfield.get('id') in extra_comments:
                extra_comments[customfield.get('id')] = (customfield.customfieldname, customfieldvalue)

        for values in extra_comments.values():
            if values:
                issue['comments'].append({ "body": '<b>%s:</b>\n\n<div>%s</div>' % (values[0], self._htmlentitydecode(values[1])) })

    def _htmlentitydecode(self, s):
        if s is None:
            return ''
        s = s.replace(' ' * 8, '')
        s = re.sub(r'(width|height)=".+?"', '', s)
        # video (not supported) -> link
        s = re.sub(r'<object.+?<embed (.+?)/></object>', lambda m: f'<a {m[1]}>video</a>'.replace(' src=', ' href='), s)
        # jira api, cache the media in beacon (video has ?stream=true)
        s = re.sub(r'/rest/api/3/attachment/content/(\d+[^"]*)', jira_attachement, s)
        # jira url
        s = re.sub(f'"{self.jiraBaseUrl}/browse/(.+?)"', lambda m: '"' + get_github_search_url(m[1], 'title') + '"', s)
        return re.sub('&(%s);' % '|'.join(name2codepoint),
                      lambda m: chr(name2codepoint[m.group(1)]), s)
