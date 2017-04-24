import argparse
import os
import re
import sys
import tempfile
import yaml
import shlex

from jinja2 import Environment, FileSystemLoader
from subprocess import Popen, PIPE
from threading import Timer

IGNORE_PATTERNS = r'\.DS_Store'
parser = argparse.ArgumentParser()


def get_all_manifests():
    path = "{}/app".format(os.path.dirname(os.path.abspath(__file__)))
    for dir_path, subdirs, files in os.walk(path):
        for f in files:
            if re.match(IGNORE_PATTERNS, f):
                continue
            yield f, dir_path


def find_manifest_path(microservice_name):
    for f, dir_path in get_all_manifests():
        if f == "{}.yml".format(microservice_name):
            return "{}/{}".format(dir_path, f)
    raise Exception("Manifest not found")


def parse_args():
    parser.add_argument("-e", "--env", help="environment to apply against")
    parser.add_argument("-m", "--microservice", help="microservice to apply")
    parser.add_argument("-i", "--image", help="docker image of microservice")
    parser.add_argument("-dmi", "--db-migration-image", help="docker image of microservice db migration")
    parser.add_argument("-d", "--dry-run", help="Do not apply. Just print all manifests to be applied",
                        action="store_true")
    parser.add_argument("-conf", "--with_configmap", help="Attach configMaps to manifest",
                        action="store_true")
    parser.add_argument("-secret", "--with_secrets", help="Attach secrests to manifest",
                        action="store_true")
    parser.add_argument("-vol", "--with_volumes", help="Attach volumes to manifest",
                        action="store_true")
    parser.add_argument("-all", "--all", help="Apply all manifests across all namespaces",
                        action="store_true")

    return parser.parse_args()


def validate_args(args):
    if not args.env:
        print "env and microservice need to be specfied."
        parser.print_help()
        sys.exit(1)
    if not (args.all or args.microservice):
        print "Either a microservice name or 'all' has to be specified to apply manifests for"
        parser.print_help()
        sys.exit(1)


def render_env_props(args, template):
    dir_path = os.path.dirname(os.path.abspath(__file__))
    env_file_path = "{}/{}/{}.yml".format(dir_path, template, args.env)
    if not os.path.isfile(env_file_path):
        raise Exception("No config found for env - {}".format(args.env))
    conf = yaml.load(open(env_file_path, "r"))
    if args.microservice:
        if not conf.has_key(args.microservice):
            conf[args.microservice] = {}
        if args.image:
            conf[args.microservice]['image'] = args.image
        if args.db_migration_image:
            conf[args.microservice]['db_migration_image'] = args.db_migration_image
    return conf


def apply_manifest(manifest):
    with tempfile.NamedTemporaryFile() as temp:
        temp.write(manifest)
        temp.flush()
        apply_cmd = "kubectl apply -f {}".format(temp.name)
        out, err = (Popen(shlex.split(apply_cmd),
                          stdout=PIPE).communicate())
        print out
        if err:
            raise Exception("Apply failed\n"
                            "STDOUT:{}\nERROR:{}".
                            format(out, err))


def wait_for_deployment_to_finish(service):
    deployment_status_cmd = "kubectl rollout status deployment/{} --all-namespaces".format(service)
    proc = Popen(shlex.split(deployment_status_cmd), stdout=PIPE)
    kill_proc = lambda p: p.kill()
    timeout_sec = 300
    timer = Timer(timeout_sec, kill_proc, [proc])
    try:
        timer.start()
        out, err = proc.communicate()
        print out
        if err:
            raise Exception("Error while checking deployment status for service: {}\n{}".format(service, err))
    finally:
        timer.cancel()


def render_manifest(args, manifest_path, template):
    env = Environment(loader=FileSystemLoader("/"),
                      trim_blocks=True)
    conf = render_env_props(args, template=template)
    return env.get_template(manifest_path).render(conf=conf)


def main():
    args = parse_args()
    validate_args(args)
    applicable_manifests = []
    cwd = os.path.dirname(os.path.abspath(__file__))

    applicable_manifests.append(render_manifest(args, manifest_path="{}/namespaces.yml".format(cwd), template='conf'))

    if args.with_configmap:
        applicable_manifests.append(
            render_manifest(args, manifest_path="{}/configMaps.yml".format(cwd), template='conf'))
    if args.with_secrets:
        applicable_manifests.append(
            render_manifest(args, manifest_path="{}/secrets.yml".format(cwd), template='secrets'))
    if args.with_volumes:
        applicable_manifests.append(render_manifest(args, manifest_path="{}/volumes.yml".format(cwd), template='conf'))

    if args.all:
        for manifest, path in get_all_manifests():
            print manifest, path
            applicable_manifests.append(render_manifest(
                args, manifest_path="{}/{}".format(path, manifest), template="conf"))
    elif args.microservice:
        applicable_manifests.append(
            render_manifest(args, manifest_path=find_manifest_path(args.microservice), template="conf"))

    final_manifest = "\n---\n".join(applicable_manifests)

    if args.dry_run:
        print final_manifest
    else:
        apply_manifest(final_manifest)
        wait_for_deployment_to_finish(args.microservice)


if __name__ == "__main__":
    main()
