#!/usr/bin/env python3
""" Nagios check for security updates

Requirements
    Python >= 3.8

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version.

This program is distributed in the hope that it will be useful, but WITHOUT
ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.
You should have received a copy of the GNU General Public License along with
this program. If not, see <http://www.gnu.org/licenses/>.
"""
import argparse
import csv
import logging
import re
import sys

from datetime import date, datetime, timedelta
from subprocess import run, TimeoutExpired, PIPE
from typing import Match, Union, Tuple

__license__ = "GPLv3"
__version__ = "0.1"

# Nagios return codes: https://nagios-plugins.org/doc/guidelines.html#AEN78
OK = 0
WARNING = 1
CRITICAL = 2
UNKNOWN = 3
return_codes = ['OK', 'WARNING', 'CRITICAL', 'UNKNOWN']
DEBUG = False

# Global logging object
logger = logging.getLogger(__name__)


def parseargs() -> argparse.Namespace:
    """ Parse command-line arguments """
    parser = argparse.ArgumentParser(description='Nagios check for security updates')
    parser.add_argument(
        '-v', '--verbose', required=False,
        help='enable verbose output', dest='verbose',
        action='store_true')
    parser.add_argument(
        '-d', '--debug', required=False,
        help='enable debug output', dest='debug',
        action='store_true')
    parser.add_argument(
        '-k', '--kernel', required=False,
        help='ommit kernel patches (if kernel live patches are enabled)', dest='nokernel',
        action='store_true')
    parser.add_argument(
        '-c', '--cache', required=False, default='/tmp/check-security-updates.cache',
        help='local cache file for patch dates (default: /tmp/check-security-updates.cache)', dest='cache',
        action='store_true')
    parser.add_argument('-V', '--version', action='version', version='%(prog)s ' + __version__)

    args = parser.parse_args()
    return args


class Firmware:
    def __init__(self, model:str = "Dell", servicetag = ""):
        self.model = model
        self.servicetag = servicetag

        # Local firmware versions
        self.bios_version = ""
        self.bmc_version = ""
        self.lifecycle_version = ""

        # Available firmware versions online
        self.bios_online = []
        self.bmc_online = []
        self.lifecycle_online = []

    def get_localfw(self):
        """Retrieve list of local firmware versions currently installed on the system"""
        pass

    def get_onlinefw(self) -> bool:
        """Retrieve list of available firmware versions online"""
        urls = {"Dell": ""}

        if self.servicetag == "":
            return False

    def check(self) -> bool:
        """Compare local to online firmware"""
        pass


class Updates:
    def __init__(self, cache_file:str, nokernel: bool=False):
        self.rc = -1
        self.critical = {}
        self.important = {}
        self.moderate = {}
        self.low = {}
        self.cache_file = cache_file
        self.nokernel = nokernel
        self.next_patchdate = None
        self.expired = False

    def run(self, cmd: list, verbose: bool=False):
        """List security updates and return result"""
        output = ""

        try:
            logger.debug(f'Running OS command line: {cmd} ...')
            process = run(cmd, check=True, timeout=60, stdout=PIPE)
            self.rc = process.returncode
            output = process.stdout.decode('utf-8').splitlines()
        except (TimeoutExpired, ValueError) as e:
            logger.warning(f'{e}')
            sys.exit(UNKNOWN)
        except FileNotFoundError as e:
            logger.critical(f"CRITICAL: Missing program {cmd[0] if len(cmd) > 0 else ''} ({e})")
            sys.exit(CRITICAL)
        except Exception as e:
            logger.critical(f'CRITICAL: {e}')
            sys.exit(CRITICAL)

        for line in output:
            expiration_date = None
            expired = None

            # Omit kernel patches
            m = re.search(r"/Sec.\s*(kernel.*)", line)
            if m and self.nokernel:
                if verbose:
                    logger.info(f"Skipping {m.group(1)}")
                continue

            # Always warn about these packages
            pkgs = "(firefox.*|chrom.*)"
            m = re.search(f"\s*{pkgs}", line)
            if m:
                logger.debug(line)
                self.critical["Critical/Sec.  " + m.group(0).strip()] = datetime.today().strftime("%Y-%m-%d")
                continue

            # Critical patches
            m = re.search(r"Critical/Sec.\s*(.*)$", line)
            if isinstance(m, Match):
                (expired, expiration_date) = self.check_expired(line, 30)
                logger.debug(line)
                self.critical[m.group(0)] = expiration_date

            # Important patches
            m = re.search(r"Important/Sec.\s*(.*)$", line)
            if isinstance(m, Match):
                (expired, expiration_date) = self.check_expired(line, 90)
                logger.debug(line)
                self.important[m.group(0)] = expiration_date

            # Moderate patches
            m = re.search(r"Moderate/Sec.\s*(.*)$", line)
            if isinstance(m, Match):
                (expired, expiration_date) = self.check_expired(line, 90)
                logger.debug(line)
                self.moderate[m.group(0)] = expiration_date

            # Low patches
            m = re.search(r"Low/Sec.\s*(.*)$", line)
            if isinstance(m, Match):
                (expired, expiration_date) = self.check_expired(line, 90)
                logger.debug(line)
                self.low[m.group(0)] = expiration_date

            if expired:
                self.expired = True

            if expiration_date:
                if not self.next_patchdate:
                    self.next_patchdate = expiration_date
                else:
                    if self.next_patchdate > expiration_date:
                        self.next_patchdate = expiration_date

        if verbose:
            # Critical
            for patch_name, expiration_date in sorted(self.critical.items(), key=lambda item: item[1] if item[1] is not None else datetime.today().date()):
                if expiration_date is None:
                    expiration_date = "-         "
                logger.info(f"Patch until {expiration_date} {patch_name}")
            # Important
            for patch_name, expiration_date in sorted(self.important.items(), key=lambda item: item[1] if item[1] is not None else datetime.today().date()):
                if expiration_date is None:
                    expiration_date = "-         "
                logger.info(f"Patch until {expiration_date} {patch_name}")
            # Medium
            for patch_name, expiration_date in sorted(self.moderate.items(), key=lambda item: item[1] if item[1] is not None else datetime.today().date()):
                if expiration_date is None:
                    expiration_date = "-         "
                logger.info(f"Patch until {expiration_date} {patch_name}")
            # Low
            for patch_name, expiration_date in sorted(self.low.items(), key=lambda item: item[1] if item[1] is not None else datetime.today().date()):
                if expiration_date is None:
                    expiration_date = "-         "
                logger.info(f"Patch until {expiration_date} {patch_name}")

            logger.info(f"Next patch date: {self.next_patchdate}")

    def create_output(self) -> Tuple[int, str]:
        """Verify result and return output in Nagios format"""
        if self.rc >= 0:
            result = OK
        else:
            return UNKNOWN, f'{return_codes[UNKNOWN]}'

        if self.expired and (len(self.important) > 0 or len(self.moderate) > 0 or len(self.low) > 0):
            result = WARNING
        if len(self.critical) > 0:
            result = CRITICAL

        msg = f'{return_codes[result]}: Critical={len(self.critical)} Important={len(self.important)} ' \
              f'Moderate={len(self.moderate)} Low={len(self.low)} next_patch_date={self.next_patchdate}'
        perfdata = f'Critical={len(self.critical)};' \
                   f'Important={len(self.important)};' \
                   f'Moderate={len(self.moderate)};' \
                   f'Low={len(self.low)};'

        message = f'{msg}|{perfdata}'
        logger.debug(message)
        return result, message

    def check_expired(self, line:str, days_limit: int) -> Tuple[bool, Union[datetime.date,None]]:
        """Check if time frame for update has expired"""
        output = ""
        expiration_date = None

        m = re.match(r"([^\s]+)\s", line)
        if m:
            logger.debug(f"{line}")
            patch = m.group(0).strip()

            # Check if patch is already in local cache
            is_cached, patch_date = self.check_cache(patch)
            if is_cached:
                logger.debug(f"Local cache: {patch} {patch_date}")
            else:
                # Retrieve patch info online
                cmd = ["yum", "updateinfo", "info", f"{patch}"]
                try:
                    logger.debug(f'Running OS command line: {cmd} ...')
                    process = run(cmd, check=True, timeout=60, stdout=PIPE)
                    self.rc = process.returncode
                    output = process.stdout.decode('utf-8').splitlines()
                except (TimeoutExpired, ValueError) as e:
                    logger.warning(f'{e}')
                    sys.exit(UNKNOWN)
                except FileNotFoundError as e:
                    logger.critical(f"CRITICAL: Missing program {cmd[0] if len(cmd) > 0 else ''} ({e})")
                    sys.exit(CRITICAL)
                except Exception as e:
                    logger.critical(f'CRITICAL: {e}')
                    sys.exit(CRITICAL)
    
                # Write patch date to cache file
                m2 = None
                for info_line in output:
                    #logger.debug(f"{info_line}")
                    m2 = re.match(r"\s*(Updated|Issued)\s*:\s*(\d+-\d+-\d+ \d+:\d+:\d+)", info_line)
                    if m2:
                        patch_date = datetime.strptime(m2.group(2), "%Y-%m-%d %H:%M:%S").date()
                        if self.update_cache(patch, patch_date):
                            logger.debug(f"Local cache updated: {patch} {patch_date}")
                        break

                if m2 is None:
                    if self.update_cache(patch, None):
                        logger.debug(f"Local cache updated: {patch} None")

            # Calculate expiration date after which patch has to be installed
            if patch_date is not None:
                expiration_date = patch_date + timedelta(days_limit)
                if date.today() >= expiration_date:
                    logger.debug(f"Timeframe to patch has expired: {expiration_date} (more than {days_limit} days ago)")
                    return True, expiration_date
                else:
                    logger.debug(f"patch_date={patch_date} days_limit={days_limit} (patch before {patch_date + timedelta(days_limit)})")
        else:
            logger.error(f"Patch line has wrong format: {line}")

        return False, expiration_date

    def check_cache(self, patch:str) -> Tuple[bool, Union[datetime.date, None]]:
        '''Check local cache for patch release date'''
        try:
            with open(self.cache_file) as csv_file:
                csv_reader = csv.reader(csv_file, delimiter=',')
                for row in csv_reader:
                    if row[0] == patch:
                        if row[1] != "None":
                            return (True, datetime.strptime(row[1], "%Y-%m-%d").date())
                        else:
                            return (True, None)
        except Exception:
            pass

        return (False, None)

    def update_cache(self, patch:str, patch_date: Union[datetime.date, None]) -> bool:
        '''Insert patch release date in local cache'''
        patch_date_str = patch_date.strftime("%Y-%m-%d") if patch_date else "None"

        try:
            with open(self.cache_file, mode='a') as csv_file:
                patch_writer = csv.writer(csv_file, delimiter=',', quotechar='"', quoting=csv.QUOTE_MINIMAL)
                patch_writer.writerow([patch, patch_date_str])
        except Exception as e:
            logger.error(f"Error writing cache file {self.cache_file}: {e}")
            return False

        return True

    def clean_cache(self) -> bool:
        '''Delete patch information from cache file that is older than 1 year'''
        patches = {}

        # Read cache file
        try:
            with open(self.cache_file) as csv_file:
                csv_reader = csv.DictReader(csv_file, delimiter=',', fieldnames=['patch_name', 'patch_date'])
                for row in csv_reader:
                    patches[row['patch_name']] = row['patch_date']
        except Exception as e:
            logger.error(f"Read error while cleaning cache file {self.cache_file}: {e}")
            return False

        # Write new cache file, sorted by patch date (newest first)
        # Patches older than 1 year are ignored
        try:
            with open(self.cache_file, mode='w') as csv_file:
                patch_writer = csv.writer(csv_file, delimiter=',', quotechar='"', quoting=csv.QUOTE_MINIMAL)
                for patch_name, patch_date in sorted(patches.items(), key=lambda kv: kv[1] if kv[1] is not None else datetime.today().date(), reverse=True):
                    try:
                        dx = datetime.today() - datetime.strptime(patch_date, "%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        dx = timedelta(0)

                    if dx.days < 365:
                        patch_writer.writerow([patch_name, patch_date])
                    else:
                        logger.debug(f"Removing from cache file: {patch_name} {patch_date}")
        except Exception as e:
            logger.error(f"Write error while cleaning cache file {self.cache_file}: {e}")
            return False

        return True


class LogFilterWarning(logging.Filter):
    """Logging filter >= WARNING"""
    def filter(self, record):
        return record.levelno in (logging.DEBUG, logging.INFO, logging.WARNING)


def get_logger(debug: bool = False) -> logging.Logger:
    """Retrieve logging object"""
    if debug:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)

    # Log everything >= WARNING to stdout
    h1 = logging.StreamHandler(sys.stdout)
    h1.setLevel(logging.DEBUG)
    h1.setFormatter(logging.Formatter(fmt='%(asctime)s [%(process)d] %(levelname)s: %(message)s',
                                      datefmt='%Y-%m-%d %H:%M:%S'))
    h1.addFilter(LogFilterWarning())

    # Log errors to stderr
    h2 = logging.StreamHandler(sys.stderr)
    h2.setFormatter(logging.Formatter(fmt='%(asctime)s [%(process)d] %(levelname)s: %(message)s',
                                      datefmt='%Y-%m-%d %H:%M:%S'))
    h2.setLevel(logging.ERROR)

    logger.addHandler(h1)
    logger.addHandler(h2)

    return logger


def main():
    """Main program flow"""
    result = OK

    args = parseargs()
    get_logger(args.debug)

    # Retrieve list of available Linux updates
    updates = Updates(args.cache, True if args.nokernel else False)
    updates.run(['yum', 'updateinfo', 'list'], args.verbose)
    result, message = updates.create_output()
    print(message)

    # Clean old entries from cache file
    updates.clean_cache()

    exit(result)


if __name__ == '__main__':
    main()
