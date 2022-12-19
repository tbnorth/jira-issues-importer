from lxml import objectify
from urllib.parse import urlencode
import os
import glob

def _exists(fn):
    if not os.path.exists(fn):
        print("Not found:", fn)
        return False
    return True


def fetch_labels_mapping():
    fn = "labels_mapping.txt"
    if not _exists(fn):
        return {}
    with open(fn) as file:
        entry = [line.split("=") for line in file.readlines() if not line.startswith('#')]
    return {key.strip(): value.strip() for key, value in entry}


def fetch_allowed_labels():
    fn = "allowed_labels.txt"
    if not _exists(fn):
        return []
    with open(fn) as file:
        return [line.strip('\n') for line in file.readlines() if not line.startswith('#')]


def fetch_people_mapping():
    fn = "people_mapping.txt"
    if not _exists(fn):
        return {}
    with open(fn) as file:
        entry = [line.split("=") for line in file.readlines() if not line.startswith('#')]
    return {value.strip(): key.strip() for key, value in entry} # {bitbucket: github}


def fetch_jira_user_mapping():
    fn = "jira_user_mapping.txt"
    if not _exists(fn):
        return {}
    with open(fn) as file:
        entry = [line.split("=") for line in file.readlines() if not line.startswith('#')]
    return {key.strip(): value.strip() for key, value in entry} # {uuid: name}


def _map_label(label, labels_mapping):
    if label in labels_mapping:
        return labels_mapping[label]
    else:
        return label


def _is_label_approved(label, approved_labels):
    return label in approved_labels


def convert_label(label, labels_mappings, approved_labels) -> str or None:
    mapped_label = _map_label(label, labels_mappings)

    if _is_label_approved(mapped_label, approved_labels):
        return mapped_label
    return None


def read_xml_file(file_path):
    with open(file_path) as file:
        return objectify.fromstring(file.read())


def read_xml_files(file_path):
    files = list()
    for file_name in file_path.split(';'):
        if os.path.isdir(file_name):
            xml_files = glob.glob(file_name + '/*.xml')
            for file in xml_files:
                files.append(read_xml_file(file))
        else:
            files.append(read_xml_file(file_name))

    return files


def get_github_search_url(term, field='comment'):
    return '../issues?' + urlencode({'q': f'in:{field} "{term}"'})
