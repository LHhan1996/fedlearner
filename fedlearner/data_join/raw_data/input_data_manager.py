import datetime
import logging
import os
from fnmatch import fnmatch

from tensorflow.compat.v1 import gfile
from fedlearner.common.common import INVALID_DATETIME, convert_to_datetime


class InputDataManager(object):
    def __init__(self, wildcard, check_success_tag,
                 single_subfolder=False,
                 files_per_job_limit=None,
                 start_date='',
                 end_date=''):
        self._wildcard = wildcard
        self._check_success_tag = check_success_tag
        self._single_subfolder = single_subfolder
        self._files_per_job_limit = files_per_job_limit
        self._start_date = convert_to_datetime(start_date)
        self._end_date = convert_to_datetime(end_date)

        self._num_files = 0
        self._num_allocated_files = 0

    @staticmethod
    def to_date(date_str):
        try:
            date_format = "%Y%m%d"
            return datetime.datetime.strptime(date_str, date_format)
        except ValueError:
            return None

    @staticmethod
    def _list_dir_helper_oss(root):
        # oss returns a file multiple times, e.g. listdir('root') returns
        #   ['folder', 'file1.txt', 'folder/file2.txt']
        # and then listdir('root/folder') returns
        #   ['file2.txt']
        filenames = set(
            os.path.join(root, i) for i in gfile.ListDirectory(root))
        res = []
        for fname in filenames:
            succ = os.path.join(os.path.dirname(fname), '_SUCCESS')
            if succ in filenames or not gfile.IsDirectory(fname):
                res.append(fname)

        return res

    def _list_dir_helper(self, root):
        filenames = list(gfile.ListDirectory(root))
        # If _SUCCESS is present, we assume there is no sub-dirs
        if '_SUCCESS' in filenames:
            return [os.path.join(root, i) for i in filenames]

        res = []
        for basename in filenames:
            fname = os.path.join(root, basename)
            if gfile.IsDirectory(fname):
                # 'ignore tmp dirs starting with _
                if basename.startswith('_'):
                    continue
                res += self._list_dir_helper(fname)
            else:
                res.append(fname)
        return res

    def _is_wanted_date(self, cur_date_str):
        cur_date = convert_to_datetime(cur_date_str)
        if cur_date != INVALID_DATETIME:
            if self._start_date != INVALID_DATETIME and \
                cur_date < self._start_date:
                return False
            if self._end_date != INVALID_DATETIME and \
                cur_date >= self._end_date:
                return False
        return True

    def list_input_dir(self, root, processed_fpath):
        logging.info("List input directory, it will take some time...")

        if root.startswith('oss://'):
            all_files = set(self._list_dir_helper_oss(root))
        else:
            all_files = set(self._list_dir_helper(root))

        self._num_files = 0
        self._num_allocated_files = len(processed_fpath)

        num_ignored = 0
        num_new_files = 0
        by_folder = {}
        for fname in all_files:
            splits = os.path.split(os.path.relpath(fname, root))
            dirnames = splits[:-1]

            # ignore files and dirs starting with _ or .
            ignore = False
            for name in splits:
                if name.startswith(('_', '.')):
                    ignore = True
                    break
            if ignore:
                num_ignored += 1
                continue

            # check wildcard
            if self._wildcard and not fnmatch(fname, self._wildcard):
                continue

            # check success tag
            if self._check_success_tag:
                succ_fname = os.path.join(root, *dirnames, '_SUCCESS')
                if succ_fname not in all_files:
                    continue

            # check dirname is wanted date
            if not self._is_wanted_date(dirnames[-1]):
                continue

            self._num_files += 1

            if fname in processed_fpath:
                continue
            num_new_files += 1

            folder = os.path.join(*dirnames)
            if folder not in by_folder:
                by_folder[folder] = []
            by_folder[folder].append(fname)

        logging.info(
            'Listing %s: found %d dirs, %d files, %d tmp files ignored, '
            '%d files matching condition, %d new files to process.',
            root, len(by_folder), len(all_files), num_ignored,
            self._num_files, num_new_files)
        return by_folder

    def iterator(self, input_path, processed_fpath):
        files_by_folder = self.list_input_dir(input_path, processed_fpath)
        while files_by_folder:
            rest_fpaths = []
            if self._single_subfolder:
                rest_folder, rest_fpaths = sorted(
                    files_by_folder.items(), key=lambda x: x[0])[0]
                logging.info(
                    'single_subfolder is set. Only process folder %s '
                    'in this iteration', rest_folder)
                del files_by_folder[rest_folder]
            else:
                rest_folders = []
                for folder, v in sorted(files_by_folder.items(),
                                        key=lambda x: x[0]):
                    if self._files_per_job_limit and rest_fpaths and \
                        len(rest_fpaths) + len(v) > self._files_per_job_limit:
                        break
                    rest_folders.append(folder)
                    rest_fpaths.extend(v)
                for folder in rest_folders:
                    del files_by_folder[folder]
            self._num_allocated_files += len(rest_fpaths)
            yield rest_fpaths

    def summary(self):
        return self._num_files, self._num_allocated_files
