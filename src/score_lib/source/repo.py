"""Read-only reads of the score repository at one commit, through git's own objects (`git show`, `git ls-tree`)."""
import fnmatch
import json
import pathlib
import subprocess


class Repo:
    def __init__(self, path, commit):
        self.path = pathlib.Path(path)
        self.commit = commit

    def git(self, *arguments, text=True):
        """One git command against the repository; its standard output."""
        done = subprocess.run(["git", "-C", str(self.path), *arguments], capture_output=True, text=text, check=True)
        return done.stdout

    def full_commit(self):
        """The full hash of the commit."""
        return self.git("rev-parse", f"{self.commit}^{{commit}}").strip()

    def files(self, prefix=""):
        """Every file path under a folder at the commit, sorted."""
        listed = self.git("ls-tree", "-r", "--name-only", "-z", self.commit, "--", prefix or ".")
        return sorted(name for name in listed.split("\0") if name)

    def exists(self, path):
        """True when the path is a file at the commit."""
        done = subprocess.run(["git", "-C", str(self.path), "cat-file", "-e", f"{self.commit}:{path}"],
                              capture_output=True)
        return done.returncode == 0

    def read_bytes(self, path):
        """A file's bytes at the commit."""
        return self.git("show", f"{self.commit}:{path}", text=False)

    def read_text(self, path):
        return self.read_bytes(path).decode()

    def read_json(self, path):
        return json.loads(self.read_bytes(path))

    def glob(self, pattern):
        """Files at the commit whose path matches a shell pattern (`data/effects/*.json`), sorted."""
        folder = pattern.split("*", 1)[0].rsplit("/", 1)[0]
        return [name for name in self.files(folder) if fnmatch.fnmatchcase(name, pattern)]

    def export(self, path, destination):
        """Write a file's bytes at the commit to a path; the path."""
        destination = pathlib.Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.read_bytes(path))
        return destination
