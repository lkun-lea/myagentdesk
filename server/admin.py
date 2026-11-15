import argparse

from dotenv import load_dotenv

from server.config import Settings
from server.db import migrate


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file")
    parser.add_argument("command", choices=["migrate"])
    args = parser.parse_args()
    if args.env_file:
        load_dotenv(args.env_file, override=True)
    settings = Settings.from_env()
    if not settings.database_url:
        parser.error("缺少 MAD_DATABASE_URL")
    migrate(settings.database_url)


if __name__ == "__main__":
    main()
