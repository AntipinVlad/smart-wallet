import asyncio
import sys

from taskiq.cli.worker.cmd import WorkerCMD

# Module level, not in main(): worker processes are spawned on Windows and import this module again.
# taskiq creates the event loop before importing app.worker, so setting it there would be too late
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def main() -> None:
    WorkerCMD().exec(["app.worker:broker", *sys.argv[1:]])


if __name__ == "__main__":
    main()
