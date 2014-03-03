pytest-docker
=============

A small test suite manager in python, using docker containers as 'test hosts'.

What is this?

This is a mini test framework that runs scripts on a group of
servers, collects the results, and compares them with the desired output.
It's assumed you are using Docker containers as your test 'hosts'.

Dependencies

* Docker (0.6 or greater)
* Paramiko

Setup

* Create your docker image and containers; each container will be treated
  as though it is a virtual host used in your testing scenario.
  See README.containers for more on this

* Make a directory 'tests'

* Make the subdirectories for your tests, and set up your data to be
  copied to the hosts, your test prep and cleanup scripts, your test
  output collection and verification scripts, and the programs you will
  testing; see README.tests for more on this

* Create top level and job level config.py files with the test configuration;
  see README.config for more on this

Running

* Start up your containers

* To run all tests you have set up, do
  python testscript.py

* To run just a specific test, do
  python testscript.py <jobname>

Bugs, comments, patches:

* visit http://github.com/apergos/pytest-docker

TODO:

  There are no example files yet; I wrote this to test a specific script
  under certain conditions and have not put together other examples.
