import argparse
import asyncio
import os
import sys
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass
from importlib.metadata import version as metadata_version
from pathlib import Path

import httpx
from PIL import Image


def main():
    sys.exit(asyncio.run(amain()))


async def amain() -> int:
    prog = 'tinicly'
    parser = argparse.ArgumentParser(
        prog=prog,
        description=f"""\
CLI for tinifying images with https://tinify.com/ and checking they're tinified.

Version: {metadata_version(prog)}
""",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    parser.add_argument('path', type=Path, help='The path to the files.')
    parser.add_argument('--token', help="The Tinify API token, falls back to the 'TINIFY_KEY' env variable.")
    parser.add_argument('--check', action='store_true', help='Check if files are tiny; fail if not.')

    args = parser.parse_args()

    if args.check:
        token = ''
    else:
        token = args.token or os.environ.get('TINIFY_KEY')
        if not token:
            print('❎ No token provided, and TINIFY_KEY not set')
            return 1

    with ThreadPoolExecutor() as executor:
        async with httpx.AsyncClient() as client:
            tinify = Tinify(executor, client, token, args.check)
            return await tinify.tinify(args.path)


def already_tiny(image_path: Path) -> bool:
    """Whether the image has already been tinified."""
    with Image.open(image_path) as image:
        # mode is always 'P' when the image has been tinified
        if image.mode != 'P':
            return False

        try:
            exif: Any = image._getexif()  # type: ignore
        except AttributeError:
            # happens on gif images, these should not be tinified
            return True

        if exif:
            # tinified images have no exif data
            return False
        return True


@dataclass
class Tinify:
    executor: Executor
    client: httpx.AsyncClient
    token: str
    check: bool
    already: int = 0
    tiny: int = 0

    async def tinify(self, path: Path) -> int:
        if path.is_file():
            await self.tinify_file(path)
        elif path.is_dir():
            await self.tinify_dir(path)
        else:
            print(f'❎ {path} does not exist')
            return 1

        if self.check:
            if self.tiny:
                print(f'❎ {self.already} files already tiny, {self.tiny} are not tiny')
                return 1
            else:
                print(f'✅ {self.already} files already tiny, no files to tinify')
        else:
            print(f'✅ {self.already} files already tiny, {self.tiny} tinified')
        return 0

    async def tinify_file(self, image_path: Path):
        """Tinify an image if it has not already been tinified."""
        loop = asyncio.get_event_loop()
        already = await loop.run_in_executor(self.executor, already_tiny, image_path)
        if already:
            self.already += 1
            print(f'already tiny: {image_path}', flush=True)
        elif self.check:
            print(f'not tinified: {image_path}', flush=True)
            self.tiny += 1
        else:
            image_content = await loop.run_in_executor(self.executor, image_path.read_bytes)
            r = await self.client.post(
                'https://api.tinify.com/shrink', auth=httpx.BasicAuth('api', self.token), content=image_content
            )
            r.raise_for_status()

            r = await self.client.get(r.headers['location'])
            r.raise_for_status()
            await loop.run_in_executor(self.executor, image_path.write_bytes, r.content)
            print(f' tinified ok: {image_path}', flush=True)
            self.tiny += 1

    async def tinify_dir(self, dir_path: Path):
        """Tinify all images in a directory if they have not already been tinified."""
        file_paths: list[Path] = []
        for g in '**/*.png', '**/*.jpeg', '**/*.jpg':
            file_paths.extend(dir_path.glob(g))
        await asyncio.gather(*[self.tinify_file(file) for file in file_paths])


if __name__ == '__main__':
    main()
