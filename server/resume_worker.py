import argparse
import signal

from dotenv import load_dotenv
import psycopg

from server.agents import AgentService
from server.config import Settings
from server.errors import Problem


def serve(service):
    while not service.stopping.is_set():
        try:
            service.drain_jobs()
        except (psycopg.Error, Problem, OSError):
            # Connection details and provider messages can contain credentials.
            pass
        service.stopping.wait(1)


def main():
    parser=argparse.ArgumentParser(description='Resume durable MyAgentDesk runs')
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    load_dotenv()
    service=AgentService(Settings.from_env())
    if args.once:
        service.drain_jobs()
    else:
        signal.signal(signal.SIGINT,lambda *_:service.stopping.set())
        signal.signal(signal.SIGTERM,lambda *_:service.stopping.set())
        serve(service)


if __name__=='__main__':
    main()
