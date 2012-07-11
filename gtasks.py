#! /usr/bin/env python
# coding=utf-8

import os
import getpass
import argparse
import keyring
import sys
import datetime
import pickle
import time
import subprocess
import gflags
import httplib2
import shutil
from hashlib import md5
from apiclient.discovery import build
from oauth2client.file import Storage
from oauth2client.client import OAuth2WebServerFlow
from oauth2client.tools import run

# Parse arguments
parser = argparse.ArgumentParser(
    usage="gtasks [arg] arg1, arg\nwith no option, default task list is printed",
        prog="gtasks 0.0.1")

parser.add_argument('--noauth_local_webserver', dest="webserver", action='store_false',
    help='validate credentials on command line only')

parser.add_argument('-i', dest="confirm", action='store_true',
    help='interactive mode, request confirmation for any changes')

parser.add_argument('-q', dest="quiet", action='store_true',
    help='quiet mode, surpress feedback messages')

parser.add_argument('--debug', dest="debug", action='store_true',
    help='debug mode')

parser.add_argument('-al', dest="add_list", action='store', nargs='?',
    help='add new list')

parser.add_argument('-el', dest="edit_list", action='store', nargs=2,
    help='edit a list\'s title')

parser.add_argument('-dl', dest="delete_list", action='store', nargs='?',
    help='delete a list')

parser.add_argument('-a', dest="add_task", action='store_true',
    help='add new task')

parser.add_argument('-e', dest="edit_task", type=int, action='store', nargs='?',
    help='edit task <number>')

parser.add_argument('-t', dest="task_title", action='store', nargs='?',
    help='set a task\'s title (used with add or edit task)')

parser.add_argument('-n', dest="task_notes", action='store', nargs='?',
    help='set a task\'s notes (used with add or edit task)')

parser.add_argument('-w', dest="task_date", action='store', nargs='?',
    help='set a task\'s date (used with add or edit task)')

parser.add_argument('-c', dest="complete_task", type=int, action='store', nargs='?',
    help='toggle task <number>\'s complete status')

parser.add_argument('-C', dest="clear_tasks", action='store_true',
    help='clear completed tasks from a list')

parser.add_argument('-d', dest="delete_task", type=int, action='store', nargs='?',
    help='delete task <number>')

parser.add_argument('-l', dest="lists", action='store', nargs="*",
    help='specify any task list(s), by title, for a command')

parser.add_argument('-L', dest="all_lists", action='store_true',
    help='use all task list(s) for a command')

parser.add_argument('-dsl', dest="show_list_after", action='store_false',
    help='don\'t show list after an action, like add/edit/delete')

parser.add_argument('-ll', dest="show_lists", action='store_true',
    help='list the task lists')

parser.add_argument('-dse', dest="show_empty_lists", action='store_false',
    help='don\'t show empty lists in task view')

parser.add_argument('-lt', dest="show_limit", type=int, action='store', nargs='?',
    help='limit number of tasks shown per list to <number>')

parser.add_argument('-sn', dest="show_task_notes", action='store_true',
    help='show the task notes')

parser.add_argument('-dsw', dest="show_task_when", action='store_false',
    help='don\'t show when the task is due')

parser.add_argument('-dsc', dest="show_task_complete", action='store_false',
    help='don\'t show completed tasks')

parser.add_argument('-sh', dest="show_task_hidden", action='store_true',
    help='show cleared tasks (hidden tasks)')

parser.add_argument('-sd', dest="show_task_deleted", action='store_true',
    help='show deleted tasks')

parser.add_argument('-st', dest="show_totals", action='store_true',
    help='show list totals')

parser.add_argument('-sdb', dest="show_due_max", action='store', nargs='?',
    help='show tasks due before <date>')

parser.add_argument('-sda', dest="show_due_min", action='store', nargs='?',
    help='show tasks due after <date> Must be used in combination with -sdb')

parser.add_argument('-db', dest="show_dashboard", action='store_true',
    help='print a dashboard')

parser.add_argument('-b', dest="bust_cache", action='store_false',
    help='bust the local cache and pull in the latest live data')

parser.add_argument('-U', dest="update_cache", action='store_true',
    help='update the task list cache and exit')

opts = parser.parse_args()

# Class for viewing and interacting with lists and tasks
class GTasks:
    _bin = os.path.realpath(__file__)
    _data_directory = os.environ['HOME'] + "/.gtasks"
    _cache_directory = _data_directory + "/gtasks.cache/"
    _cache_lock = _cache_directory + "lock"
    _settings_file = _data_directory + "/gtasks.settings"
    _dat_file = _data_directory + "/gtasks.dat"
    _UTCDIFF = datetime.datetime.utcnow() - datetime.datetime.now()
    today_date = datetime.datetime.now() - _UTCDIFF
    today_date = today_date.date()

    confirm = False
    silent = False
    debug = False
    TTL = 300
    TTLL = 300

    if not os.path.exists(_data_directory):
        os.makedirs(_data_directory)
    if not os.path.exists(_cache_directory):
        os.makedirs(_cache_directory)

    def __init__(self):
        if os.path.isfile(self._settings_file):
            self._settings = pickle.load( open(self._settings_file, "rb" ) )
        else:
            self._settings = {}
            try:
                list = Google_Tasks().service().tasklists().get(tasklist='@default').execute()
                self._settings['default_list'] = list['title']
            except Exception as err:
                if self.debug:
                    print err['args']
                    print err
                self._feedback('Error, problem talking with google api')
                sys.exit(1)
        self.default_list = self._settings['default_list']
        if 'clear_cache_date' in self._settings:
            expired_cache = self._settings['clear_cache_date'] + datetime.timedelta(days=int(-2))
            if self.today_date > expired_cache:
                self._clear_old_cache()
            self._settings['clear_cache_date'] = expired_cache
        else:
            self._settings['clear_cache_date'] = self.today_date
        self._save_settings()

    def _save_settings(self):
        pickle.dump(self._settings, open(self._settings_file, 'wb'))

    # output errors and statuses
    def _feedback(self, string):
        if self.silent != True:
            print string

    # get lists as a combination of cached and live
    def _get_lists(self, show=[], required_lists=[], use_cache=True):
        found_lists = []
        missing_lists = []
        live_lists = []

        if use_cache:
            # distill the right lists from the cache
            cached_lists = self._get_cached_lists(show, required_lists)
            if cached_lists:
                found_lists = cached_lists['found']
                missing_lists = cached_lists['missing']
            if self.debug:
                if found_lists:
                    for list in found_lists:
                        print '"' + list.resource['title'] + '" found in cache'
                if missing_lists:
                    for title in missing_lists:
                        print '"' + title + '" missing from cache'

        if missing_lists:
            # lists missing from cache, look for them on live
            live_lists = self._get_live_lists(show, missing_lists)
        elif not found_lists:
            # no (matching) lists in cache, try live
            live_lists = self._get_live_lists(show, required_lists)
        elif not required_lists:
            # a cache hit was made, update the cache in background
            self._background_update(show, required_lists)

        if live_lists:
            # distill the right lists from live
            found_lists.extend(live_lists['found'])
            missing_lists = live_lists['missing']
            if self.debug:
                if found_lists:
                    for list in found_lists:
                        print '"' + list.resource['title'] + '" found in api'
                if missing_lists:
                    for title in missing_lists:
                        print '"' + title + '" missing from api'

        if not required_lists:
            # if not in a required order, sort lists alphabetically
            found_lists = sorted(found_lists, key=lambda key: key.resource['title'].lower())

        for title in missing_lists:
            self._feedback('Error, list "' + title + '" not found')

        return {'found' : found_lists, 'missing' : missing_lists}

    def _clear_old_cache(self):
        cache_files = os.listdir(GTasks._cache_directory)
        cur_time = time.time()
        for file in cache_files:
            cache_file_path = GTasks._cache_directory + file
            cache_time = cur_time - os.path.getmtime(cache_file_path)
            if cache_time > GTasks.TTLL:
                os.remove(cache_file_path)

    # get lists from cache
    def _get_cached_lists(self, show=[], required_lists=[]):
        found_lists = []
        cache_files = os.listdir(GTasks._cache_directory)
        expired_lists = []
        # show hash used to identify list
        # lists stored as md5(title)-md5(show_hash)
        show_hash = md5(str(show['data'])).hexdigest()

        if required_lists:
            missing_lists = required_lists[:]
            for title in required_lists:
                title_hash = md5(str(title)).hexdigest()
                key = title_hash + '-' + show_hash
                if key in cache_files:
                    cache_file_path = GTasks._cache_directory + key
                    found_lists.append(pickle.load( open(cache_file_path, "rb" ) ))
                    missing_lists.remove(title)
        else:
            missing_lists = []
            for cache_file in cache_files:
                if cache_file[(cache_file.find('-') +1):] == show_hash:
                    cache_file_path = GTasks._cache_directory + cache_file
                    found_lists.append(pickle.load( open(cache_file_path, "rb" ) ))

        # from the found lists, check if any have expired
        # currently we return the list anyway, updating it in the background
        cur_time = time.time()
        for list in found_lists:
            cache_file_path = GTasks._cache_directory + list.cache_key
            cache_time = cur_time - os.path.getmtime(cache_file_path)
            if cache_time > GTasks.TTL:
                os.remove(cache_file_path)
                expired_lists.append(list.resource['title'])

        if expired_lists:
            # background update of the expired lists
            self._background_update(show, expired_lists)

        return {'found' : found_lists, 'missing' : missing_lists}

    # get lists from google api ("live")
    def _get_live_lists(self, show=[], required_lists=[]):
        all_lists = {}
        found_lists = []
        live_lists = []

        try:
            lists_resources = Google_Tasks().service().tasklists().list().execute()
        except Exception as err:
            if self.debug:
                print err['args']
                print err
            self._feedback('Error, problem talking with google api')
            return {'found' : [], 'missing' : required_lists}

        for list in lists_resources['items']:
            all_lists[list['title']] = list

        if required_lists:
            missing_lists = required_lists[:]
            for title in required_lists:
                if title in all_lists:
                    live_lists.append(all_lists[title])
                    missing_lists.remove(title)
        else:
            missing_lists = []
            for title in all_lists:
                live_lists.append(all_lists[title])

        for list in live_lists:
            found_lists.append(List(list, show))

        for list in found_lists:
            # create list objects from the found lists and cache them
            list.cache_key = self._cache_key(list.resource['title'], show['data'])
            self._cache_list(list, list.cache_key)

        return {'found' : found_lists, 'missing' : missing_lists}

    # Update list(s) in subprocess
    def _background_update(self, show, required_lists):
        arguments = ['-U', '-q']
        if not show['data']['complete']:
            arguments.extend(['-dsc'])
        if show['data']['deleted']:
            arguments.extend(['-sd'])
        if show['data']['hidden']:
            arguments.extend(['-sh'])
        if show['data']['due_max']:
            arguments.extend(['-sdb'])
            arguments.extend([show['data']['due_max'][0:10]])
        if show['data']['due_min']:
            arguments.extend(['-sda'])
            arguments.extend([show['data']['due_min'][:10]])
        if show['data']['limit']:
            arguments.extend(['-lt'])
            arguments.extend([show['data']['limit']])
        if required_lists:
            arguments.extend(['-l'])
            for list in required_lists:
                arguments.extend([list])
        else:
            arguments.extend(['-L'])
        command = [GTasks._bin]
        command.extend(arguments)
        subprocess.Popen(command)

    # generate a key (filename) for caching a list
    def _cache_key(self, title, show_data):
        title_hash = md5(str(title)).hexdigest()
        show_hash = md5(str(show_data)).hexdigest()
        key = title_hash + '-' + show_hash
        return key

    # cache a list
    def _cache_list(self, list, key):
        pickle.dump(list, open(GTasks._cache_directory + key, 'wb'))

    # remove cached lists
    def clear_cached_lists(self, list_titles=[]):
        cache_files = os.listdir(GTasks._cache_directory)
        if list_titles:
            for title in list_titles:
                title_hash = md5(str(title)).hexdigest()
                for cache_file in cache_files:
                    if cache_file.find(title_hash) != -1:
                        os.remove(GTasks._cache_directory + cache_file)
        else:
            shutil.rmtree(GTasks._cache_directory)
            os.makedirs(GTasks._cache_directory)

    # replace the cached lists with live data
    # creates a lock file so only one update can happen at a time
    def update_cached_lists(self, show, required_lists = []):
        if not os.path.isfile(GTasks._cache_lock):
            try:
                with file(GTasks._cache_lock, 'a'):
                    os.utime(GTasks._cache_lock, None)
                live_lists = self._get_live_lists(show, required_lists)
                os.remove(GTasks._cache_lock)
            except:
                os.remove(GTasks._cache_lock)

    # convenience function for when interacting with a task
    def confirm_or_exit(self, question):
        if not self.confirm:
            return
        confirm = raw_input(question + '"? [y/n] ')
        if confirm != 'y':
            sys.exit(0)

    # convenience function to return the task in a list to interact with
    def _get_task(self, list, task_number, exit_on_error = True):
        try:
            task = list.tasks[task_number]
        except:
            self._feedback('Error, task not found')
            if exit_on_error:
                sys.exit(1)
            return
        return task

    # convenience function to return the list to interact with
    def _get_list(self, show, list, use_cache = True, exit_on_error = True):
        lists = self._get_lists(show, list, use_cache)['found']
        if not lists:
            if exit_on_error:
                sys.exit(1)
            return
        return lists[0]

    # add a list
    def add_list(self, show, list):
        self.confirm_or_exit('Add list: "' + list + '"')
        list = {'title': list}
        try:
            result = Google_Tasks().service().tasklists().insert(body=list).execute()
            if result:
                list = List(result, show)
                list.cache_key = self._cache_key(list.resource['title'], show['data'])
                self.clear_cached_lists([list.resource['title']])
                self._cache_list(list, list.cache_key)
                self._feedback('List "' + list.resource['title'] + '" added')
            else:
                raise Exception()
        except Exception as err:
            if self.debug:
                print err['args']
                print err
            self._feedback('Error, problem adding new list')

    # delete a list
    def delete_list(self, show, list):
        self.confirm_or_exit('Delete list: "' + list + '"')
        list = self._get_list(show, [list])
        try:
            result = Google_Tasks().service().tasklists().delete(tasklist=list.id).execute()
            self.clear_cached_lists([list.resource['title']])
            self._feedback('List "' + list.resource['title'] + '" deleted')
        except Exception as err:
            if self.debug:
                print err['args']
                print err
            self._feedback('Error, problem deleting list')

    # edit list title
    def edit_list(self, show, list, new_title):
        if not new_title:
            self._feedback('Error, missing new list title')
            return
        self.confirm_or_exit('Edit list: "' + list + '"')
        list = self._get_list(show, [list])

        list.resource['title'] = new_title

        try:
            result = Google_Tasks().service().tasklists().delete(tasklist=list.id).execute()
            self.clear_cached_lists([list.resource['title']])
            self._cache_list(list, list.cache_key)
            self._feedback('List "' + list.resource['title'] + '" updated')
        except Exception as err:
            if self.debug:
                print err['args']
                print err
            self._feedback('Error, problem updating list')


    # add a new task to a list
    def add_task(self, show, list, task):
        self.confirm_or_exit('Add task: "' + task['title'] + '"')
        if not task['title']:
            self._feedback('Error, missing title. Task not added')
            return
        list = self._get_list(show, list)
        task['due'] = task['date']

        try:
            result = Google_Tasks().service().tasks().insert(tasklist=list.id, body=task).execute()
            if self.debug:
                print result
            if result['id']:
                self._feedback('Task "' + task['title'] + '" added')
            else:
                raise Exception()
        except Exception as err:
            if self.debug:
                print err['args']
                print err
            self._feedback('There was an error adding the task')
        self.clear_cached_lists([list.resource['title']])
        self._background_update(show, [list.resource['title']])

    # edit a task
    def edit_task(self, show, list, edit_task):
        list = self._get_list(show, list)
        task = self._get_task(list, edit_task['position'])
        self.confirm_or_exit('Update task: "' + task.resource['title'] + '"')

        action = 'updated'
        if edit_task['toggle_complete']:
            if task.resource['status'] == 'completed':
                action = 'marked incomplete'
                task.resource['status'] = 'needsAction'
                del task.resource['completed']
            else:
                action = 'marked complete'
                task.resource['status'] = 'completed'
        elif edit_task['toggle_delete']:
            if task.deleted:
                action = 'undeleted'
                del task.resource['deleted']
            else:
                action = 'deleted'
                task.resource['deleted'] = True
        else:
            task.resource['title'] = edit_task['title']
            task.resource['notes'] = edit_task['notes']
            if edit_task['date']:
                task.resource['due'] = edit_task['date']

        try:
            result = Google_Tasks().service().tasks().update(tasklist=list.id, task=task.id, body=task.resource).execute()
            if result or task.deleted:
                self._feedback('Task "' + result['title'] + '" ' + action)
            else:
                raise Exception()
        except Exception as err:
            if self.debug:
                print err['args']
                print err
            self._feedback('Error, there was a problem updating the task')
        self.clear_cached_lists([list.resource['title']])
        self._background_update(show, [list.resource['title']])


    # clear completed tasks from a list
    def clear_tasks(self, show, list):
        list = self._get_list(show, list)
        try:
            Google_Tasks().service().tasks().clear(tasklist=list.id).execute()
            self._feedback('Completed tasks cleared from list "' + list.resource['title'] + '"')
        except Exception as err:
            if self.debug:
                print err['args']
                print err
            self._feedback('Error, problem talking with google api')
        self.clear_cached_lists([list.resource['title']])
        self._background_update(show, [list.resource['title']])

    # Calculate the combination of all lists/tasks in the current GTasks object
    def _combine_list_totals(self, lists):
        totals = {}
        for value in lists[0].totals:
            for list in lists:
                if not value in totals:
                    totals[value] = 0
                totals[value] += list.totals[value]
        return totals

    def _print_totals(self, totals):
        print ('Total: {0[total]:d}; Complete: {0[complete]:d}; ' \
            'Incomplete {0[incomplete]:d};'.format(totals))

        print ('Overdue: {0[overdue]:d}; Due Today: {0[due_today]:d}; ' \
            'Due this week: {0[due_this_week]:d}; Due sometime: {0[due_sometime]:d}; ' \
            'Never due: {0[due_never]:d};'.format(totals))

    def show_dashboard(self, show, required_lists=[], use_cache = True):
        lists = self._get_lists(show, required_lists, use_cache)['found']
        totals = self._combine_list_totals(lists)

        print Task_Color.BAKRED + '[' + str(totals['overdue']) + ']' + \
            Task_Color.BAKYEL + '[' + str(totals['due_today']) + ']' + \
            Task_Color.BAKBLU + '[' + str(totals['due_this_week']) + ']' + \
            Task_Color.TXTDEF

    def show_lists(self, show, required_lists=[], use_cache = True):
        lists = self._get_lists(show, required_lists, use_cache)['found']

        for list in lists:
            print list.resource['title']
            if show['display']['totals']:
                self._print_totals(list.totals)
        if show['display']['totals']:
            print
            self._print_totals(self._combine_list_totals(lists))

    # display tasks
    def show_tasks(self, show=[], required_lists=[], use_cache=True):
        # get the lists to work with
        lists = self._get_lists(show, required_lists, use_cache)['found']

        # calculate the max with of task number for a consistent gutter
        max_list_width = 0
        for list in lists:
            list_tasks_count = len(list.tasks)
            if list_tasks_count > max_list_width:
                max_list_width = list_tasks_count
        notes_gutter = '         ' + (' ' * len(str(max_list_width)))

        # check for task status values for a consistent gutter
        task_status = ''
        if show['data']['complete'] or show['data']['deleted'] or show['data']['hidden']:
            for list in lists:
                for task in list.tasks:
                    if task.complete or task.deleted or task.hidden:
                        task_status = '  '
                        break

        shown_lists = 0
        shown_tasks = 0
        for list in lists:
            task_count = len(list.tasks)
            if not show['display']['empty_lists'] and task_count == 0:
                continue
            print
            print list.resource['title']
            print '-' * len(list.resource['title'])
            if task_count == 0:
                print 'empty list'
            if list.tasks:
                for task in list.tasks:
                    shown_tasks = shown_tasks +1
                    gutter = ' ' * (int(len(str(max_list_width)))- int(len(str(task.position))))
                    status = task_status
                    if show['data']['complete']:
                        if task.complete:
                            status = ' ✓'
                    if show['data']['deleted']:
                        if task.deleted:
                            status = ' ✗'
                    task_when = ''
                    if show['display']['when']:
                        if task.due_status:
                            task_when = '➪ ' + Task_Color.status(
                                task.due_status, task.complete,
                                task.due_in_days_status + ', ' + task.due_date)
                        elif task.complete:
                            task_when = '➪ ' + Task_Color.status(
                                task.due_status, task.complete,
                                task.complete_days_status + ', ' + task.complete_date)
                    print gutter, str(task.position) + '.' + status, \
                        task.resource['title'], ' ' + task_when
                    if show['display']['notes']:
                        if 'notes' in task.resource:
                            print notes_gutter + task.resource['notes'].replace('\n', '\n' + notes_gutter)

            print
            if show['display']['totals']:
                self._print_totals(list.totals)
                print
            shown_lists = shown_lists +1

        if show['display']['totals']:
            if int(shown_lists) > 1:
                print 'OVERALL TOTALS'
                self._print_totals(self._combine_list_totals(lists))
                print

        if shown_tasks == 0:
            print 'no tasks found'



# Format task output
class Task_Color:
    # Colors
    TXTDEF='\033[0m'          # everything back to defaults
    TXTRED='\033[0;31;1m'     # red text
    TXTBLU='\033[0;34;1m'     # blue text
    TXTYEL='\033[0;33;1m'     # yellow text
    BLDWHT='\033[0;37;1m'     # white text
    BAKPUR='\033[37;1;45m'    # purple background
    BAKBLU='\033[37;1;44m'    # blue background
    BAKRED='\033[37;1;41m'    # red background
    BAKGRN='\033[37;1;42m'    # green background
    BAKYEL='\033[37;1;43m'    # yellow background
    status_colors = {
        'none' : TXTDEF,
        'overdue' : TXTRED,
        'sometime' : TXTBLU,
        'today' : BAKYEL,
        'this week' : TXTYEL}
    @staticmethod
    def status(status, complete, string):
        if complete:
            status_color = Task_Color.TXTDEF
        else:
            status_color = Task_Color.status_colors[status]
        return status_color + string + Task_Color.TXTDEF


# List class
# Has a list of Task instances
# Keeps a running total of various states of the tasks
# For convenience has its own id property, but all google task specific data
# is contained in "resource", used whenever communicating to the google api
class List:
    def __init__(self, resource, show):
        self.resource = resource
        self.show = show
        self.id = resource['id']
        self._get_tasks()
        self.calibrate()

    def calibrate(self):
        self._set_task_positions()
        self.totals = {
            'total' : 0,
            'complete' : 0,
            'incomplete' : 0,
            'overdue' : 0,
            'due' : 0,
            'due_today' : 0,
            'due_this_week' : 0,
            'due_sometime' : 0,
            'due_never' : 0}
        for task in self.tasks:
            self.totals['total'] += 1
            if task.due_status is not 'none' and not task.complete:
                if task.due_status != 'overdue':
                    self.totals['due'] += 1
                if task.due_status == 'overdue':
                    self.totals['overdue'] += 1
                elif task.due_in_days == 0:
                    self.totals['due_today'] += 1
                elif task.due_in_days <= 7:
                    self.totals['due_this_week'] += 1
                elif task.due_in_days > 7:
                    self.totals['due_sometime'] += 1
            else:
                self.totals['due_never'] += 1
            if task.complete:
                self.totals['complete'] += 1
            else:
                self.totals['incomplete'] += 1

    def _set_task_positions(self):
        i = 1
        for task in self.tasks:
            task.position = i
            i += 1

    # get tasks for the list
    # paginate for tasks as necessary - making more calls to the api
    # makes use of "show" object which details what kind of task data to retrieve
    def _get_tasks(self):
        self.tasks = []
        tasks_resource = {'nextPageToken' : None}
        while 'nextPageToken' in tasks_resource:
            tasks_resource = Google_Tasks().service().tasks().list(
                tasklist=self.id,pageToken=tasks_resource['nextPageToken'],
                showCompleted=self.show['data']['complete'],
                showDeleted=self.show['data']['deleted'],
                showHidden=self.show['data']['hidden'],
                dueMin=self.show['data']['due_min'],
                dueMax=self.show['data']['due_max'],
                maxResults=self.show['data']['limit']
                ).execute()

            if 'items' in tasks_resource:
                for task in tasks_resource['items']:
                    if self.show['data']['limit']:
                        if len(self.tasks) >= self.show['data']['limit']:
                            return
                    self.tasks.append(Task(task))

# Task class
# For convenience has its own id property, but all google task specific data
# is contained in "resource", used whenever communicating to the google api
# Has a number of own properties to augment the google api resource properties
class Task:
    def __init__(self, resource):
        # use GTasks date, which is modified for local time
        today_date = GTasks.today_date
        self.resource = resource
        self.id = resource['id']

        self.due_in_days = ''
        self.due_in_days_status = ''
        self.due_date=''
        self.due_status = None

        self.complete = False
        self.deleted = False
        self.hidden = False

        # determine tasks status for:
        # - complete
        # - deleted
        # - hidden (complete and then cleared)
        # - due
        if 'completed' in resource:
            self.complete = True
            complete_date = self._convert_RFC_date(self.resource['completed'])
            self.complete_date = str(complete_date)
            complete_in = str(abs(today_date - complete_date))
            complete_in = complete_in[0:-9]
            if complete_in == '':
                self.complete_days_status = 'completed today'
            else:
                self.complete_days_status = 'completed ' + complete_in + ' ago'
        if 'deleted' in resource:
            self.deleted = True
        if 'hidden' in resource:
            self.hidden = True
        if not self.complete and 'due' in resource:
            due_date = self._convert_RFC_date(self.resource['due'])
            self.due_date = str(due_date)
            due_in = str(abs(today_date - due_date))[0:-9]
            self.due_in_days = due_in[0:due_in.find(' ')]
            if self.due_in_days == '':
                self.due_in_days = 0
            else:
                self.due_in_days = int(self.due_in_days)
            if self.due_in_days == 0:
                self.due_in_days_status = 'due today'
            if today_date > due_date:
                self.due_status = 'overdue'
                self.due_in_days_status = 'overdue ' + due_in + ' ago'
            elif today_date == due_date:
                self.due_status = 'today'
            elif self.due_in_days <= 7:
                self.due_status = 'this week'
                self.due_in_days_status = 'due in ' + due_in
            else:
                self.due_status = 'sometime'
                self.due_in_days_status = 'due in ' + due_in

    # convert RFC date to python date
    def _convert_RFC_date(self, rfc_utc_date):
        local_date = datetime.datetime(
            int(rfc_utc_date[0:4]),
            int(rfc_utc_date[5:7]),
            int(rfc_utc_date[8:10]),
            int(rfc_utc_date[11:13]),
            int(rfc_utc_date[14:16]),
            int(rfc_utc_date[17:19])
        )
        return local_date.date()

# Class for handling connection to the Google Tasks api
class Google_Tasks:
    _connection = False
    _storage = Storage(GTasks._dat_file)
    _credentials = _storage.get()
    _gtasks_user = getpass.getuser()
    _gtasks_key = keyring.get_password('gtasks_key', _gtasks_user)

    def __init__(self):
        Google_Tasks._check_credentials()

    # If the Credentials don't exist or are invalid, run through the authentication
    # flow. The Storage object will ensure that if successful the good
    # Credentials will get written back to a file.
    @staticmethod
    def _check_credentials():
        if Google_Tasks._credentials is None or Google_Tasks._credentials.invalid == True:
            gtasks_id = keyring.get_password('gtasks_id', Google_Tasks._gtasks_user)
            gtasks_secret = keyring.get_password('gtasks_secret', Google_Tasks._gtasks_user)

            FLOW = OAuth2WebServerFlow(
                client_id=gtasks_id,
                client_secret=gtasks_secret,
                scope='https://www.googleapis.com/auth/tasks',
                user_agent='gtasks/0.0.1')

            FLAGS = gflags.FLAGS
            FLAGS.auth_local_webserver = opts.webserver

            Google_Tasks._credentials = run(FLOW, Google_Tasks._storage)

    # Return a service object to use to talk to the api
    def service(self):
        if not Google_Tasks._connection:
            http = httplib2.Http(GTasks._data_directory + '/http.cache')

            http = Google_Tasks._credentials.authorize(http)
            Google_Tasks._connection = build(serviceName='tasks', version='v1',
                http=http, developerKey=Google_Tasks._gtasks_key)
        return Google_Tasks._connection


### Opts args interpretation

# Function to unique a list
def f7(seq):
    seen = set()
    seen_add = seen.add
    return [ x for x in seq if x not in seen and not seen_add(x)]

# create an RFC date string from a user specified date
def interpret_date(format_date):
    if not format_date:
        return
    if format_date.find('-') == 4:
        try:
            ud = format_date.split('-')
            format_date = datetime.date(int(ud[0]), int(ud[1]), int(ud[2])).isoformat()
        except:
            sys.exit('Error, problem with date supplied')
    else:
        days_of_week = {
            'lastmon' : 1, 'lasttue' : 2, 'lastwed' : 3, 'lastthu' : 4,
            'lastfri' : 5, 'lastsat' : 6, 'lastsun' : 7,
            'mon' : 1, 'tue' : 2, 'wed' : 3, 'thu' : 4,
            'fri' : 5, 'sat' : 6, 'sun' : 7,
            'nextmon' : 8, 'nexttue' : 9, 'nextwed' : 10, 'nextthu' : 11,
            'nextfri' : 12, 'nextsat' : 13, 'nextsun' : 14}
        if format_date in days_of_week:
            current_day = GTasks.today_date.isoweekday()
            requested_day = days_of_week[format_date]
            if format_date.find('last') != -1:
                day_delta = requested_day - current_day
                if day_delta >= 0:  # = means go back one week if same day
                    day_delta = day_delta - 7
            else:
                day_delta = requested_day - current_day
                if day_delta < 0: # no = means treat as today if day matches
                    day_delta = (7 + requested_day) - current_day
        elif format_date == 'today' or format_date == 'tod':
            day_delta = 0
        elif format_date == 'tomorrow' or format_date == 'tom':
            day_delta = 1
        elif format_date == 'yesterday'or format_date == 'yes':
            day_delta = -1
        elif format_date == 'lastweek':
            day_delta = -7
        else:
            day_delta = format_date

        try:
            format_date = GTasks.today_date + datetime.timedelta(days=int(day_delta))
            format_date = format_date.isoformat()
        except Exception:
            sys.exit('Error, problem with date supplied')
    return format_date + 'T00:00:00.000Z'

if opts.lists:
    # unique the titles
    opts.lists = f7(opts.lists)

if opts.show_task_hidden:
    opts.show_task_complete = True

if opts.show_due_min or opts.show_due_max:
    opts.show_task_when = True

if opts.show_due_min and not opts.show_due_max:
    sys.exit('Error, -sda must be used in combination with -sdb')

show = {
    'display': {
        'notes' : opts.show_task_notes,
        'when' : opts.show_task_when,
        'totals' : opts.show_totals,
        'empty_lists' : opts.show_empty_lists
        },
    'data' : {
        'complete' : opts.show_task_complete,
        'deleted' : opts.show_task_deleted,
        'hidden' : opts.show_task_hidden,
        'due_max' : interpret_date(opts.show_due_max),
        'due_min' : interpret_date(opts.show_due_min),
        'limit' : (opts.show_limit)
    }
}

task = {
    'title' : opts.task_title,
    'notes' : opts.task_notes,
    'date' : interpret_date(opts.task_date),
    'toggle_complete' : False,
    'toggle_delete' : False
}

try:
    # Create the GTasks object
    GTasks = GTasks()
    # put GTasks into requested modes
    GTasks.confirm = opts.confirm
    GTasks.silent = opts.quiet
    GTasks.debug = opts.debug

    amend_task = False

    if not opts.lists and not opts.show_lists and not opts.all_lists:
        opts.lists = [GTasks.default_list]
    elif opts.all_lists:
        opts.lists = []

    if opts.update_cache:
        # Update the GTask cache and exit
        GTasks.update_cached_lists(show, opts.lists)
        sys.exit(0)
    elif opts.show_dashboard:
        GTasks.show_dashboard(show, opts.lists, opts.bust_cache)
    elif opts.add_list:
        GTasks.add_list(show, opts.add_list)
    elif opts.edit_list:
        GTasks.edit_list(show, opts.edit_list[0], opts.edit_list[1])
    elif opts.delete_list:
        GTasks.delete_list(show, opts.delete_list)
    elif opts.clear_tasks == True:
        amend_task = True
        GTasks.clear_tasks(show, opts.lists)
    elif opts.add_task:
        amend_task = True
        GTasks.add_task(show, opts.lists, task)
    elif opts.edit_task:
        amend_task = True
        task['position'] = opts.edit_task -1
        GTasks.edit_task(show, opts.lists, task)
    elif opts.complete_task:
        amend_task = True
        task['position'] = opts.complete_task -1
        task['toggle_complete'] = True
        GTasks.edit_task(show, opts.lists, task)
    elif opts.delete_task:
        amend_task = True
        task['position'] = opts.delete_task -1
        task['toggle_delete'] = True
        GTasks.edit_task(show, opts.lists, task)
    elif opts.show_lists == True:
        GTasks.show_lists(show, opts.lists, opts.bust_cache)
    else:
        GTasks.show_tasks(show, opts.lists, opts.bust_cache)

    if amend_task and opts.show_list_after:
        # show the list after an action has taken place
        GTasks.show_tasks(show, opts.lists, opts.bust_cache)

except KeyboardInterrupt:
      # do nothing here
      print
      pass
except Exception as err:
    if opts.debug:
        print err
        print 'Unexpected Error'
    else:
        print 'Error, unknown error, run with `--debug` for details'