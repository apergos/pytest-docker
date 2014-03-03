import os
import sys
import re
import httplib
import getopt
import json
import socket
import traceback

VERSION = "0.1"

class LocalHTTPConnection(httplib.HTTPConnection):
    def __init__(self, socket_name, timeout=socket._GLOBAL_DEFAULT_TIMEOUT):
        httplib.HTTPConnection.__init__(self, 'localhost', timeout=timeout)
        self.socket_name = socket_name

    def connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self.socket_name)
        sock.settimeout(None)
        self.sock = sock

class Docker(object):
    def __init__(self, docker):
        self.docker = docker
        self.hosts_file = "/etc/hosts"
        self.header = "# docker additions"

    def get_container_name(self, instance):
        url = "/containers/" + instance + "/json"
        output = self.get_url(url)
        result = output['Name'].strip().lstrip('/')
        return result

    def set_container_name(self, instance, name):
        # takes instance id and tag (human-readable name)
        raise ValueError("Sorry kids but we cannot (re)name a container via the API.. yet.")

    def convert_ids_to_names(self, ids):
        # if any of the passed in ids are not container names,
        # get those instead; if no name is available, returns "id_" + container id
        # and tags the container with that too :-)
        # returns: list of the container names
        names = []
        for instance in ids:
            # name might be what was passed in but we don't care
            name = self.get_container_name(instance)
            if not name:
                name = "id_" + instance
                self.set_container_name(instance, name)
            names.append(name)
        return names

    def get_image_name(self, repo, tag):
        return repo + ":" + tag

    def get_url(self, url, method='GET', content=None):
        try:
            httpConn = LocalHTTPConnection("/var/run/docker.sock", timeout=20)
        except:
            print "failed to establish http connection to localhost for docker"
            raise

        h = {"User-Agent": "test-docker-api.py"}
        if content:
            h["Content-Type"] = "application/json"

        httpConn.request(method, url, body=content, headers=h)
        response = httpConn.getresponse(buffering=True)
        data = response.read()
        if (response.status == 200 or response.status == 201 or
            response.status == 204):
            if data:
                return json.loads(data.decode('utf-8'))
            else:
                return ""
        else:
            if data:
                sys.stderr.write(data + "\n")
            raise IOError('failed to get url ' + url, " with response code " + str(response.status))

    def get_hosts_file(self, container_name):
        url = "/containers/" + container_name + "/json"
        output = self.get_url(url)
        result = output['HostsPath'].strip()
        if not result:
            sys.stderr.write('got: ' + output + "\n")
            raise DockerError('Failed to get hosts file name for ' + container_name)
        return result

    def get_ip(self, instance_name):
        url = "/containers/" + instance_name + "/json"
        output = self.get_url(url)
        result = output['NetworkSettings']['IPAddress'].strip()
        if not result or not self.is_ip(result):
            # fixme output is a dict not a string d'oh
            sys.stderr.write('got: ' + output + "\n")
            raise DockerError('Failed to get ip of ' + instance_name)
        return result

    # fixme this is only ipv4... which is fine for right now
    def is_ip(self, string):
        try:
            fields = string.split('.')
        except:
            return False
        if not len(fields) == 4:
            return False
        for f in fields:
            if not f.isdigit():
                return False
            if int(f) > 255:
                return False
        return True

    def clean_my_etc_hosts(self, ips_hosts, cleanall, dryrun):
        new_content = []
        with open(self.hosts_file,'r') as f:
            while 1:
                # read each line
                e = f.readline().rstrip('\n')
                if not e:
                    break
                if not e.endswith("# docker added"):
                    new_content.append(e)
                    continue
                if cleanall:
                    continue
                ip = e.split(e)[0]
                if ip in ips_hosts:
                    continue
                else:
                    new_content.append(e)

        if dryrun:
            print "would write to", self.hosts_file
            print "\n".join(new_content) + "\n"
        else:
            with open(self.hosts_file + ".new",'w') as f:
                f.write("\n".join(new_content) + "\n")
            os.rename(self.hosts_file + ".new", self.hosts_file)

    def update_my_etc_hosts(self, ips_hosts, dryrun):
        new_content = []
        with open(self.hosts_file,'r') as f:
            while 1:
                # read each line

                e = f.readline().rstrip('\n')
                if not e:
                    break
                if e.lstrip().startswith('#'):
                    new_content.append(e)
                    continue
                # ip hostname aliases
                ip = e.split(e)[0]
                if ip in ips_hosts:
                    del ips_hosts[ip]
                else:
                    new_content.append(e)

        new_content.extend([ ip + "   " + ips_hosts[ip] + "   # docker added" for ip in ips_hosts ])

        if dryrun:
            print "would write to", self.hosts_file
            print "\n".join(new_content) + "\n"
        else:
            with open(self.hosts_file + ".new",'w') as f:
                f.write("\n".join(new_content) + "\n")
            os.rename(self.hosts_file + ".new", self.hosts_file)

    def truncate_etc_hosts(self, instance_name, dryrun):
        # truncate hosts file right after the docker additions comment line,
        # if any
        # return true is there was a docker additions line
        # false if not

        # we will hack the file listed in HostsPath for the container. too bad
        hosts_file = self.get_hosts_file(instance_name)
        if not hosts_file:
            return

        with open(hosts_file,'r') as f:
            while 1:
                # read each line
                e = f.readline().rstrip('\n')
                if not e:
                    return False
                # toss any entries made by previous runs
                if e.startswith(self.header):
                    if dryrun:
                        print "would truncate file, old information present"
                    else:
                        f.truncate()
                    return True

    def clean_etc_hosts(self, instance_name, dryrun):
        self.truncate_etc_hosts(instance_name, dryrun)

    def update_etc_hosts(self, instance_name, ips_hosts, dryrun):
        self.truncate_etc_hosts(instance_name, dryrun)

        entries = [ ip + "   " + ips_hosts[ip] for ip in ips_hosts if ips_hosts[ip] != instance_name ]
        contents = "\n".join([ self.header ] + entries) + "\n"

        # fixme this is inefficient, we already get the hosts file path
        # once during truncation
        hosts_file = self.get_hosts_file(instance_name)
        if not hosts_file:
            return

        if dryrun:
            print "would write to", hosts_file
            print contents
        else:
            with open(hosts_file,'a') as f:
                f.write(contents)

    def update_hosts_files(self, instances, clean=False, cleanall=False, dryrun=False):
        instance_names = self.convert_ids_to_names(instances)

        ips = {}
        for name in instance_names:
            ip = self.get_ip(name)
            ips[ip] = name

        for name in instance_names:
            if clean:
                self.clean_etc_hosts(name, dryrun)
            else:
                self.update_etc_hosts(name, ips, dryrun)

        if clean:
            self.clean_my_etc_hosts(ips, cleanall, dryrun)
        else:
            self.update_my_etc_hosts(ips, dryrun)

def usage(message = None):
    if message is not None:
        sys.stderr.write(message)
        sys.stderr.write("\n")
    help_text = """Usage: setup-etc-hosts.py --ids name[,name,name...]
                   [--docker path] [--clean] [--cleanall] [--version] [--help]

This script updates the /etc/hosts files of the specified containers
with the names and ips of all containers in the list.  If container
ids are provided the corresponding container names will be looked
up and used.

The script also updates /etc/host on the host where it is running
with that same information.

Options:

  --ids       (-i)  names or ids of containers, separated
                    by a comma
  --docker    (-d)  full path to docker executable
                    default: '/usr/bin/docker'
  --clean     (-c)  clean the names and ips from /etc/hosts
                    instead of adding them
  --cleanall  (-C)  clean all docker-added names and ips from /etc/hosts
                    including those from previous runs of this script
                    for other containers or hosts
  --version   (-v)  display version information and exit
  --help      (-h)  show this message and exit
"""
    sys.stderr.write(help_text)
    sys.exit(1)

def show_version():
    print "setup-etc-hosts.py " + VERSION
    sys.exit(0)

if __name__ == '__main__':
    docker = "/usr/bin/docker"
    dryrun = False
    verbose = False
    clean = False
    cleanall = False
    ids = []

    try:
        (options, remainder) = getopt.gnu_getopt(sys.argv[1:], "cCd:i:vh", ["clean", "cleanall", "docker=", "ids=", "dryrun", "version","help"])
    except getopt.GetoptError as err:
        usage("Unknown option specified: " + str(err))
    for (opt, val) in options:
        if opt in ["-d", "--docker" ]:
            docker = val
        elif opt in ["-c", "--clean" ]:
            clean = True
        elif opt in ["-C", "--cleanall" ]:
            clean = True
            cleanall = True
        elif opt in ["-i", "--ids" ]:
            ids = val.split(',')
        elif opt in ["--dryrun"]:
            dryrun = True
        elif opt in ["-v", "--version" ]:
            show_version()
        elif opt in ["h", "--help" ]:
            usage()
        else:
            usage("Unknown option specified: <%s>" % opt)

    if len(remainder) > 0:
        usage("Unknown option(s) specified: <%s>" % remainder[0])

    if not len(ids):
        usage("Mandatory argument ids not given")

    d = Docker(docker)
    d.update_hosts_files(ids, clean, cleanall, dryrun)
