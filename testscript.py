import os, sys, time, socket, paramiko, subprocess, re, getopt
import runpy

# TODOs

# * verify doesn't write to output and we don't capture and display it either
#   FIXMEEEEE

# * do_ssh and do_sftp might oughta have timeouts and whines in case of timeout
# * make sure I have config sample files as I claim to have
# * might be nice to be able to run the jobs in a specified order

class Testjob(object):
    def __init__(self, base, job):
        """base: path to directory containing jobs,
                 e.g. '.../tests/jobs'
        job: name of a subdir under 'jobs'"""

        self.base = base
        self.name = job
        job_config = self.get_conf()
        if os.path.exists(job_config):
#            execfile(job_config)
            g = runpy.run_path(job_config)
            self.jobopts = g['jobopts']
        else:
            self.jobopts = None

    def get_job(self):
        return(os.path.join(self.base, self.name))

    def get_conf(self):
        return(os.path.join(self.get_job(), "config.py"))

    def get_tmp(self):
        return(os.path.join(self.get_job(), "tmp"))

    def get_log(self):
        return(os.path.join(self.get_tmp(), "log.txt"))

    def get_patch(self):
        return(os.path.join(self.get_job(), 'patch'))

class Sshhost(object):
    def __init__(self, host, password, verbose, dryrun):
        self.host = host
        self.password = password
        self.dryrun = dryrun
        self.verbose = verbose
        self.conn = None

    def do_ssh(self, command, log=None, exitonfail=False):
        if not self.conn:
            self.conn = paramiko.SSHClient()
            self.conn.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            if not self.dryrun:
                self.conn.connect(self.host, username='root', password=self.password)

        if self.dryrun:
            print "on host", self.host, "would run command:", command
            return

        if self.verbose:
            print "on host", self.host, "running command:", command

        try:
            ssh_stdin, ssh_stdout, ssh_stderr = self.conn.exec_command(command)
        except:
            if exitonfail:
                print "failed"
                raise

        if ssh_stdout:
            logit(log, ssh_stdout.read())
        if ssh_stderr:
            logit(log, ssh_stderr.read())

    def do_sftp(self, filename, location, retrieve=False, log=None, exitonfail=False):
        if not self.conn:
            self.conn = paramiko.SSHClient()
            self.conn.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.conn.connect(self.host, username='root', password=self.password)

        if self.dryrun:
            if retrieve:
                print "would retrieve",
            else:
                print "would put",
            print filename, "to/from", location, "on host", self.host
            return

        if self.verbose:
            if retrieve:
                print "getting",
            else:
                print "putting",
            print filename, "to/from", location, "on host", self.host

        try:
            ftp = self.conn.open_sftp()
            if location.endswith(os.path.sep):
                location = os.path.join(location, os.path.basename(filename))
            if retrieve:
                ftp.get(location, filename)
            else:
                ftp.put(filename, location)
            ftp.close()
        except:
            print "failed"
            if exitonfail:
                raise

def logit(log, content):
    if log:
        logfd = open(log, "a+")   # create if not there right?
    else:
        logfd = sys.stderr        # hm and no stdout use?

    if content and content.strip():
        logfd.write(content)

    if logfd != sys.stderr:
        logfd.close()

def do_command(command, log=None, exitonfail=False):
    errs = None
    out = None
    try:
        proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        (out, errs) = proc.communicate()
    except:
        if exitonfail:
            print "failed"
            if errs:
                print errs
            raise

    if out:
        logit(log, out)
    if errs:
        logit(log, errs)

class Test(object):
    def __init__(self, base, opts, job=None, quiet=False, verbose=False, dryrun=False, ignores=None):
        """base: base dir for test suite
        opts: opts dict from top level config
        jobs: list of job dirs under base/jobs
        quiet: display no progress messages
        verbose: display extra progress messages
        dryrun: display commands but don't run them
        ignores: file names to ignore when looking up files"""

        self.base = base
        if job:
            jobs = [ job ]
        else:
            jobs = self.get_filenames(os.path.join(self.base,'jobs'), ignores)
        self.jobs = [ Testjob(os.path.join(self.base, 'jobs'), j) for j in jobs ]

        self.opts = opts
        self.quiet = quiet
        self.dryrun = dryrun
        self.verbose = verbose
        self.joblog = None

    def get_tmp(self):
        return(os.path.join(self.base, "tmp"))

    def no_ignore(self, filename, ignores):
        for i in ignores:
            if re.search(i, filename):
                return False
        return True

    def get_filenames(self, dirname, ignores):
        files = os.listdir(dirname)
        return [ f for f in files if self.no_ignore(f,ignores) ]

    def get_hosts_from_configs(self):
        hosts = []
        for stanza in self.opts:
            for item in self.opts[stanza]:
                if 'hosts' in self.opts[stanza][item]:
                    hosts.extend(self.opts[stanza][item]['hosts'])
        if 'default' in self.opts and 'hosts' in self.opts['default']:
            hosts.extend(self.opts['default']['hosts'])
        for j in self.jobs:
            for stanza in j.jobopts:
                for item in j.jobopts[stanza]:
                    more_hosts = self.get_attr('hosts', stanza, item, j)
                    if hosts:
                        hosts.extend(more_hosts)
        return list(set(hosts))

    def setup(self):
        """Set up before test suite:
          open ssh connections to all hosts,
          set hostnames on each host, overriding the container name"""

        print "doing setup"
        self.ssh_conns = {}
        self.all_hosts = self.get_hosts_from_configs()
        for h in self.all_hosts:
            if h not in self.ssh_conns:
                self.ssh_conns[h] = Sshhost(h, opts['globals']['password'], self.verbose, self.dryrun)

        # we could reset them at the end but who cares, next container startup they will
        # have new random names anyways
        for h in self.all_hosts:
            self.ssh_conns[h].do_ssh('echo %s > /proc/sys/kernel/hostname' % h, exitonfail=True)

    def apply_patch(self, patch, scriptname, job):
        """pass in the full path to the script for scriptname,
        the filename of the patch as it lives in the job tree
           or the full path to it,
        and the job object

        returns: the full path to the patched script"""

        if job is not None:
            patched_script = os.path.join(job.get_tmp(), os.path.basename(scriptname))
            patch_path = os.path.join(job.get_patch(), patch)
        else:
            self.whine("patches are a per job item.")

        if os.path.exists(patched_script):
            os.unlink(patched_script)

        status =  os.stat(patch_path)
        if status.st_size > 0:
            command = [ 'patch', '-o', patched_script, scriptname, patch_path ]
        else:
            command = [ 'cp', scriptname, patched_script ]
        if self.dryrun:
            print "would run commmand:", command
            return(patched_script)

        if self.verbose:
            print "running command:", command

        do_command(command, log=self.joblog, exitonfail=True)
        return(patched_script)

    def get_attr(self, attr, stanza, item, job):
        """Given an attribute like 'untar' or 'remote',
        find the value of it for the given stanza ('main', 'data', etc)
        by checking first the per-job config, if there is one, and
        falling back if needed to the top-level config."""
        if job and job.jobopts and stanza in job.jobopts and attr in job.jobopts[stanza][item]:
            return job.jobopts[stanza][item][attr]

        if stanza in self.opts:
            if item in self.opts[stanza] and attr in self.opts[stanza][item]:
                return self.opts[stanza][item][attr]
            elif '*' in self.opts[stanza] and attr in self.opts[stanza]['*']:
                return self.opts[stanza]['*'][attr]

        if 'default' in self.opts and attr in self.opts['default']:
            return self.opts['default'][attr]

        return None

    def job_has_item(self, job, stanza, item):
        """job:     Testjob object for a specific job
           stanza:  'data', 'main', 'prep' etc
           item:    a script or filename to be run, installed, etc
                    in this phase of the test suite
           returns: full path to item, or None on failure
        """
        item_path = os.path.join(job.get_job(), stanza, item)
        if not os.path.exists(item_path):
            return None
        else:
            return item_path

    def get_host_results_path(self, job, h):
        if job:
            tmp = job.get_tmp()
        else:
            tmp = self.get_tmp()
        return os.path.join(tmp, "results_" + h)

    def put_stanza(self, stanza, message=None, job=None):
        self.display_progress(message)
        if job and job.jobopts and stanza in job.jobopts:
            todo = job.jobopts[stanza]
        else:
            todo = self.opts[stanza]

        for item in todo:
            # skip wildcard item
            if item == '*':
                continue

            if job:
                local_file = self.job_has_item(job, stanza, item)
                if not local_file:
                    continue
            else:
                local_file = os.path.join(self.base, stanza, item)

            if self.get_attr('untar', stanza, item, job) is not None:
                remote = opts['globals']['tmp']
            else:
                remote = self.get_attr('remote', stanza, item, job)
            if not remote:
                continue

            patch = self.get_attr('patch', stanza, item, job)
            if patch is not None:
                local_file = self.apply_patch(patch, local_file, job)

            hosts = self.get_attr('hosts', stanza, item, job)

            for h in hosts:
                self.ssh_conns[h].do_ssh("mkdir -p " + remote,
                                         log=self.joblog, exitonfail=True)
                self.ssh_conns[h].do_sftp(local_file, remote,
                                          log=self.joblog, exitonfail=True)
                if 'execute' in todo[item] and todo[item]['execute'] == '':
                    self.ssh_conns[h].do_ssh("chmod 0755 " +
                                             os.path.join(remote, item),
                                             log=self.joblog, exitonfail=True)
                if 'untar' in todo[item]:
                    untarred_remote = self.get_attr('remote', stanza, item, job)
                    self.ssh_conns[h].do_ssh("cd %s ; zcat %s | tar xvfp -"
                                             % (untarred_remote,
                                                os.path.join(remote, item)),
                                             log=self.joblog, exitonfail=True)

    def run_stanza(self, stanza, message=None, job=None, exitonfail=True):
        self.display_progress(message)
        if job and job.jobopts and stanza in job.jobopts:
            todo = job.jobopts[stanza]
        else:
            todo = self.opts[stanza]

        # fixme don't we want verify stuff to go to stdout? so not everything to
        # go to log file...

        for item in todo:
            if job:
                local_file = self.job_has_item(job, stanza, item)
                if not local_file:
                    continue
            else:
                local_file = os.path.join(self.base, stanza, item)

            execute = self.get_attr('execute', stanza, item, job)
            if execute is None:
                continue

            remote = self.get_attr('remote', stanza, item, job)
            if remote is None:
                # subprocess Popen wants a list
                command = [ local_file ]
            else:
                # ssh wants a string
                command = os.path.join(self.get_attr('remote', stanza, item, job),item)

            if execute != '':
                if remote:
                    # ssh wants a string
                    command = execute + " " + command
                else:
                    # subprocess Popen wants a list
                    command = [ execute ] + command

            if remote is None:
                if self.dryrun:
                    print "would run command:", command
                    return

                if self.verbose:
                    print "running command:", command

                do_command(command, log=self.joblog, exitonfail=exitonfail)
            else:
                hosts = self.get_attr('hosts', stanza, item, job)
                for h in hosts:
                    self.ssh_conns[h].do_ssh(command, log=self.joblog, exitonfail=exitonfail)

    def get_stanza(self, stanza, message=None, job=None, exitonfail=True):
        self.display_progress(message)
        if job and job.jobopts and stanza in job.jobopts:
            todo = job.jobopts[stanza]
        else:
            todo = self.opts[stanza]

        for item in todo:
            local_file = self.job_has_item(job, stanza, item)
            if not local_file:
                continue

            remote =  self.get_attr('results', stanza, item, job)
            if remote is None:
                continue

            hosts = self.get_attr('hosts', stanza, item, job)
            for h in hosts:
                local_file = self.get_host_results_path(job, h)
                if os.path.exists(local_file):
                    os.unlink(local_file)
                self.ssh_conns[h].do_sftp(local_file, remote, retrieve=True,
                                          log=self.joblog, exitonfail=True)

    def display_progress(self, message=None):
        if message and not self.quiet:
            print message

    def run(self):
        self.put_stanza('prep', message='doing prep on hosts')
        self.run_stanza('prep')

        for job in self.jobs:
            if not os.path.isdir(job.get_job()):
                print "no directory for job",j,"continuing"
                continue

            self.do_job_setup(job)

            self.put_stanza('main', message='copying main script(s) to hosts', job=job)
            self.put_stanza('data', message='copying data to hosts', job=job)
            self.put_stanza('mod', message='copying mod scripts to hosts', job=job)
            self.put_stanza('collect', message='copying collection scripts to hosts', job=job)
            self.put_stanza('verify', message='copying verification scripts to hosts', job=job)

            self.run_stanza('mod', message="modifying data etc on hosts", job=job)
            self.run_stanza('main', exitonfail = False, message="running script on hosts", job=job)
            self.run_stanza('collect', exitonfail = False, message="collecting test suite output on hosts", job=job)
            self.get_stanza('collect', exitonfail = False, job=job)

            results = os.path.join(self.get_tmp(), "results_%s.txt" % job.name)
            if os.path.exists(results):
                os.unlink(results)
            self.joblog = results
            self.run_stanza('verify', exitonfail = False, message="verifying results", job=job)
            self.joblog = None

            if os.path.exists(results):
                with open(results, 'r') as f:
                    print f.read()
            else:
                print "no results file for this job"

            self.do_job_cleanup(job)

            print 'done!'

        self.put_stanza('cleanup', message='doing cleanup on hosts')
        self.run_stanza('cleanup')

    def get_host_results_path(self, job, h):
        return os.path.join(job.get_tmp(), "results_" + h)

    def do_job_setup(self, job):
        if not os.path.isdir(job.get_tmp()):
            os.mkdirs(job.get_tmp())
        self.joblog=job.get_log()
        if os.path.exists(self.joblog):
            os.unlink(self.joblog)

        print '******************'
        print "doing job", job.name
        print '******************'

    def do_job_cleanup(self, job):
        self.joblog = None

VERSON = "0.1"

def usage(message=None):
    if message:
        sys.stderr.write(message + "\n")
    help_message = """Usage: testscript.py [--job jobname] [--quiet] [--verbose] [--version] [--help]

Options:

job     (-j):  name of directory under tests/jobs which contains a particular job you
               want to run; if not specified, all jobs in the test suite will be run
quiet   (-q):  don't display progress messages, only the verification output
verbose (-v):  display extra progress messages, primarily used for debugging
dryrun  (-d):  don't run the test suite but only display the commands that would be executed
version (-V):  display the name and version of this program and exit
help    (-h):  display this help message
"""
    sys.stderr.write(help_message)
    sys.exit(1)

def show_version():
    print "testscript.py %s" % VERSION
    sys.exit(0)

if __name__ == '__main__':
    verbose = False
    dryrun = False
    quiet = False
    job = None

    try:
        (options, remainder) = getopt.gnu_getopt(
            sys.argv[1:], "j:vVqdh", [ "job=", "verbose", "quiet",
                                      "dryrun", "version", "help" ] )
    except:
        usage("Unknown option specified")

    for (opt, val) in options:
        if opt in ["-j", "--job"]:
            job = val
        elif opt in ["-v", "--verbose"]:
            verbose = True
        elif opt in ["-q", "--quiet"]:
            quiet = True
        elif opt in ["-d", "--dryrun"]:
            dryrun = True
        elif opt in ["-h", "--help"]:
            usage("Help")
        elif opt in ["-V", "--version"]:
            show_version()
        else:
            usage("Unknown option specified: <%s>" % opt)

    if len(remainder) > 0:
        usage("Unknown option specified: <%s>" % remainder[0])


    opts = None
#    execfile('tests/config.py')
    g = runpy.run_path('tests/config.py')
    opts = g['opts']
    ignores = [ '~$' ]

    t = Test("tests", opts, job, quiet, verbose, dryrun, ignores=ignores)
    t.setup()

    t.run()
