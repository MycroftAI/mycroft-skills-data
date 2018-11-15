#!/usr/bin/env python3
#
# Copyright 2018 Mycroft AI Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
import sys
import urllib.parse
from collections import OrderedDict

import json
import os
import shutil
import re
from difflib import SequenceMatcher
from git import Repo, Git
from github import Github, GithubException
from github.ContentFile import ContentFile
from github.Repository import Repository
from os import isatty
from os.path import dirname, abspath, isfile, join, isdir
from prettyparse import create_parser
from tempfile import gettempdir
from typing import Optional

# NOTE: The versions of MSM and MSK referenced in the requirements.txt
#       determines which branch of the mycroft-skills repo is being harvested.
from msk.util import ask_for_github_credentials, register_git_injector
from msm import MycroftSkillsManager, SkillEntry, SkillRepo

DEFAULT_BRANCH = '18.08'

# Enter username and password as strings to avoid typing while testing, etc.
github_username = None
github_password = None
use_branch = None  # defaults to "18.08"


usage = '''
Generates a JSON file containing info on all Mycroft Skills referenced by the
mycroft-skills repository (https://github.com/MycroftAI/mycroft-skills)

:-o --output-file str -
    Output json file

:-u --upload
    Whether to upload result to mycroft-skills-data repo
'''

root = dirname(abspath(__file__))


##########################################################################
# Utilities

class TempClone:
    """Create a clone in a temp dir used to write and push file changes"""
    def __init__(self, url: str, branch: str=None):
        import posixpath
        self.path = join(gettempdir(), posixpath.basename(url))
        if not isdir(self.path):
            Repo.clone_from(url, self.path)
        self.git = Git(self.path)
        self.git.fetch()
        if branch:
            self.git.checkout(branch)

    def write(self, path: str, content: str):
        with open(join(self.path, path), 'w') as f:
            f.write(content)
        self.git.add(path)
        self.git.commit(message="Automatic update of skill-metadata.json")
        self.git.push()

    def delete(self):
        shutil.rmtree(self.path)


def load_github() -> Github:
    """ Create Github API object """
    if isfile(join(root, 'token.txt')):
        # Get token from file
        with open(join(root, 'token.txt')) as f:
            token = f.read().strip()
        register_git_injector(token, '')
        return Github(token)
    elif os.environ.get('GITHUB_TOKEN'):
        # Get token from environment variable
        token = os.environ['GITHUB_TOKEN']
        register_git_injector(token, '')
        return Github(token)
    elif github_username and github_password:
        # Use credentials entered above
        github = Github(github_username, github_password)
        github.get_user().login
        register_git_injector(github_username, github_password)
        return github
    elif isatty(sys.stdout.fileno()):
        # Interactive
        return ask_for_github_credentials()
    else:
        print('Warning: No authentication. May exceed GitHub rate limit')
        return Github()


def upload_summaries(github: Github, summaries: dict, branch: str=None):
    print('Uploading skill-metadata.json...')
    repo = github.get_repo('MycroftAI/mycroft-skills-data')  # type: Repository
    branch = branch or DEFAULT_BRANCH
    if not repo.permissions.push:
        print('You don\'t have write permissions')
        exit(1)
    clone = TempClone('https://github.com/mycroftai/mycroft-skills-data',
                      branch)
    clone.write('skill-metadata.json', json.dumps(summaries, indent=4))
    clone.delete()

##########################################################################
# README.md parsing/formatting


def extract_sections(readme_content: str) -> OrderedDict:
    """ Split README.md markdown into sections
    Returns:
        {
            'Header Title': 'content under\nheader',
            'Header2 Title': 'content under\nheader2',
            ...
        }
    """
    last_section = ''
    sections = OrderedDict({last_section: ''})
    for line in readme_content.split('\n'):
        line = line.strip()
        if line.startswith('# ') or line.startswith('## '):
            last_section = line.strip('# ')
            sections[last_section] = ''
        else:
            sections[last_section] += '\n' + line
    for section_name in list(sections):
        sections[section_name] = sections[section_name].strip()
    sections[''] = sections.pop('')  # Shift to end
    return sections


def compare(a: str, b: str) -> float:
    return SequenceMatcher(a=a.lower(), b=b.lower()).ratio()


def norm(x: str) -> str:
    """ Normalize string for comparison between:
        skill-names
    and
        skill name
    """
    return x.lower().replace('-', ' ')


def find_section(name: str, sections: dict,
                 min_conf: float = 0.5) -> Optional[str]:
    """ Return the section with heading that matches `name` most closely """
    title, conf = max([(title, compare(title, name)) for title in sections],
                      key=lambda x: x[1])

    return None if conf < min_conf else sections[title]


def format_sentence(s: str) -> str:
    """ 'this is a test' -> 'This is a test.' """
    s = caps(s)
    if s and s[-1].isalnum():
        return s + '.'
    return s


def caps(s: str) -> str:
    """ Capitalize first letter without lowercasing the rest"""
    return s[:1].upper() + s[1:]


def parse_example(example: str) -> str:
    """ "hey mycroft, what is this" -> What is this? """
    example = example.strip(' \n"\'`')
    example = re.split(r'["`]', example)[0]

    # Remove "Hey Mycroft, "
    for prefix in ['hey mycroft', 'mycroft', 'hey-mycroft']:
        if example.lower().startswith(prefix):
            example = example[len(prefix):]
    example = example.strip(' ,')  # Fix ", " from "Hey Mycroft, ..."
    if any(
            example.lower().startswith(word + suffix + ' ')
            for word in ['who', 'what', 'when', 'where']
            for suffix in ["'s", "s", "", "'d", "d" "'re", "re"]
    ):
        example = example.rstrip('?.') + '?'
    example = format_sentence(example)
    return example


def find_examples(sections: dict) -> list:
    """
    Example: {'Examples': ' - "Hey Mycroft, how are you?"\n - "Hey Mycroft, perform test" <<< Does a test'}  # nopep8
    Returns: ['How are you?', 'Perform test.']
    """
    return re.findall(
        string=(find_section('examples', sections) or
                find_section('usage', sections) or ''),
        pattern=r'(?<=[-*]).*', flags=re.MULTILINE
    )


def find_title_info(sections: dict, skill_name: str) -> tuple:
    """ Extract title from first section

    Handles both:
        # <img src=.../> My skill
    and
        # My skill

    Returns:
        title (string), short_desc (string)
    """
    # Get title from the section with an icon
    title_section = None
    for section in sections:
        if "<img" in section:
            title_section = section
            break
    if not title_section:
        # Attempt old scheme - first section header is the title
        title_section = next(iter(sections))
        return title_section, ""   # Should never be allowed in repo!

    # Remove traces of any <img> tag that might exist, get text that follows
    title = title_section.split("/>")[-1].strip()
    short_desc = sections[title_section]
    return title, short_desc


def find_icon(sections: dict, repo: str, tree: str) -> tuple:
    # Get first section's title (icon is in the title itself), like:
    # <img src='https://rawgi...' card_color='maroon' height='50'/> Skill Name
    # Get first section's title

    # Get section name with the icon
    title_section = None
    for section in sections:
        if "<img" in section:
            title_section = section
            break
    if not title_section:
        return None, None, None

    url = None
    name = None
    color = None
    prev = ''
    for part in title_section.split("'"):
        part = part.strip()
        if prev.endswith("src="):
            url = part
        elif prev.endswith("card_color="):
            color = part
        prev = part

    # Check if URL is a Font Awesome preview image
    if url and url.startswith("https://rawgithub.com/FortAwesome/Font-Awesome"):
        # Break down down just the filename part, e.g.
        #   "https://rawgithub...vg/solid/microchip.svg" -> "microchip"
        name = url.split('/')[-1].split(".")[0]
        url = None
    elif url:
        if not urllib.parse.urlparse(url).netloc:
            # Assume this is a local reference, expand it to a full-path
            url = (repo.replace("github.com", "raw.githubusercontent.com") +
                   '/' + tree + '/' + url)

    return url, name, color


def make_credits(lines: str) -> list:
    # Convert multiline credits into list
    # Ex:
    #   @acmcgee\nMycroftAI (@MycroftAI)\nTom's great songs
    result = []
    for line in lines.splitlines():
        words = []
        username = None
        for word in line.split():
            word = word.strip("()")
            if word.startswith("@"):
                username = word[1:]
            else:
                words.append(word)
        if words and username:
            result.append({"name": " ".join(words),
                           "github_id": username})
        elif words:
            result.append({"name": " ".join(words)})
        elif username:
            result.append({"github_id": username})

    return result


def generate_summary(github: Github, skill_entry: SkillEntry):
    """
    Generate an entry for a Skill that has been accepted to the
    Mycroft Skills repo (https://github.com/mycroft-skills).

    {
       "mycroft-reminder": {
            # repo url
            "repo": "https://github.com/MycroftAI/skill-reminder",
            # branch of the repo
            "branch": "18.08",
            # Exact commit accepted
            "tree": "https://github.com/MycroftAI/skill-reminder/tree/afb9d3387e782de19fdf2ae9ec6d2e6c83fee48c",
            # name for the folder on disk, e.g. /opt/mycroft/skills/mycroft-reminder.mycroftai
            "name": "mycroft-reminder",
            "github_username": "mycroftai",

            # Used in titles and for 'Hey Mycroft, install ...'
            "title": "Set reminders",

            # One of the following two entries
            "icon" : {"name": "info", "color": "#22a7f0" },
            "icon_img" : "https://somewhere.org/picture.png",

            # List of credited contributors.  Some might not have a github_id
            # such as when crediting a song.
            "credits" : [
                {"name": "Mycroft AI", "github_id": "MycroftAI"},
                {"name": "Reminder tone from Tony of Tony's Sounds"}
            ],

            # The tagline description
            "short_desc": "Set single and repeating reminders for tasks",

            # The detailed description.  Can contain markdown
            "description": "Flexible reminder Skill, allowing you to set single and repeating reminders for tasks. The reminders are set on the Device, and have no external dependencies. ",

            # Example phrases.  Order counts, first is most representative.
            "examples": [
                "Set a reminder every day to take my vitamin pill.",
                "Remind me to put the garbage out at 8pm.",
                "Remind me to walk the dog in an hour.",
                "Set a reminder every Friday at 2pm.",
                "Remind me to stretch in 10 minutes."
            ],

            # Categories.  Order counts, first is the primary category.
            "categories": ["Daily","Productivity"],

            # Tags are arbitrary and order has no meaning.
            "tags": ["reminder", "reminders"],

            # Supported platforms by name, or "all"
            "platforms": ["platform_mark1"]
        }
    }
    """
    if github:
        author = skill_entry.extract_author(skill_entry.url)
        repo_name = skill_entry.extract_repo_name(skill_entry.url)
        repo_id = '/'.join([author, repo_name])
        repo = github.get_repo(repo_id)  # type: Repository
        repo_url = repo.html_url
        readme_file = repo.get_readme()  # type: ContentFile
        readme = readme_file.decoded_content.decode()
    else:
        readme = skill_entry.readme
        repo_url = "http://dummy.url"
    sections = extract_sections(readme)
    title, short_desc = find_title_info(sections, skill_entry.name)

    entry = {
        'repo': repo_url,
        # 'branch': "18.08",              # TODO: repo.branch,
        'tree': skill_entry.sha,
        'name': skill_entry.name,
        'github_username': skill_entry.author,

        'title': title,
        'short_desc': format_sentence(short_desc.replace('\n',
                                                         ' ')).rstrip('.'),
        'description': format_sentence(find_section('About', sections) or
                                       find_section('Description', sections) or
                                       ''),

        'examples': [parse_example(i) for i in find_examples(sections)],

        'credits': make_credits((find_section('Credits',
                                              sections, 0.9) or
                                 caps(skill_entry.author))),
        'categories': [
            cat.replace('*', '') for cat in sorted((find_section('Category',
                                                    sections,
                                                    0.9) or '').split())],
        'platforms': (find_section('Supported Devices',
                                   sections, 0.9) or 'all').split(),
        'tags': (find_section('Tags',
                              sections) or '').replace('#', '').split()
    }

    icon_url, icon_name, icon_color = find_icon(sections, repo_url,
                                                skill_entry.sha)
    if icon_url:
        entry["icon_img"] = icon_url
    elif icon_name:
        entry["icon"] = {"icon": icon_name, "color": icon_color}

    return entry


##########################################################################


def main():
    args = create_parser(usage).parse_args()

    github = load_github()

    summaries = {}
    repo = SkillRepo(path=join(gettempdir(), 'mycroft-skills-repo'),
                     branch=use_branch)
    print("Working on repository: ", repo.url)
    print("               branch: ", repo.branch)
    for skill_entry in MycroftSkillsManager(repo=repo).list():
        if not skill_entry.url:
            continue
        print('Generating {}...'.format(skill_entry.name))
        try:
            summary = generate_summary(github, skill_entry)
        except GithubException as e:
            print('Failed to generate summary:', repr(e))
            continue
        summaries[skill_entry.name] = summary

    if args.output_file:
        with open(args.output_file, 'w') as f:
            json.dump(summaries, f)
    else:
        print(json.dumps(summaries, indent=4))
    if args.upload:
        upload_summaries(github, summaries, use_branch)


def test_main():
    # Use this
    TEST_README = """
    For rapid bugfix/testing, you can paste a README.md here
    """

    class FauxSkillEntry:
        @property
        def name(self):
            return "picroft_example_skill_gpio"

        @property
        def author(self):
            return "MycroftAI"

        @property
        def readme(self):
            return TEST_README

    print(json.dumps(
            generate_summary(None, FauxSkillEntry()),
            indent=4))


if __name__ == '__main__':
    # test_main()
    main()
