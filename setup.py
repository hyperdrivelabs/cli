# Copyright 2019 Farzad Senart and Lionel Suss. All rights reserved.
#
# This file is part of IC CLI.
#
# IC CLI is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# IC CLI is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with IC CLI. If not, see <https://www.gnu.org/licenses/>.

import pathlib

import setuptools
import setuptools.command.build_py


class build_py(setuptools.command.build_py.build_py):
    """Specialize Python source builder to exclude '_test.py' files."""

    # pylint: disable=invalid-name

    def find_package_modules(self, package, package_dir):
        modules = super().find_package_modules(package, package_dir)
        return filter(
            lambda m: not m[2].endswith("_test.py")
            and not "/testdata/" in m[2]
            and m[2].rpartition("/")[-1] != "conftest.py",
            modules,
        )


setuptools.setup(
    name="iccli",
    version="0.3.1",
    license="AGPL-3.0-only",
    description="compose, share, and deploy cloud infrastructure bricks",
    long_description_content_type="text/markdown",
    long_description=pathlib.Path("README.md").read_text(),
    url="https://ic.dev",
    project_urls={
        "Source": "https://github.com/icdotdev/cli",
        "Issues": "https://github.com/icdotdev/cli/issues",
    },
    packages=setuptools.find_packages(where="src"),
    package_dir={"": "src"},
    include_package_data=True,
    cmdclass={"build_py": build_py},
    entry_points={"console_scripts": ["ic=iccli.cmd.cmd_group:main"]},
    python_requires="~=3.7",
    setup_requires=["pip ~= 19.1", "setuptools ~= 41.0", "wheel ~= 0.33"],
    extras_require={
        "dev": [
            "awscli",
            "black",
            "mypy",
            "pylint",
            "pytest",
            "pytest-cov",
            "pytest-flask",
            "pytest-mock",
            "pytest-xdist",
            "pylintfileheader",
            "twine",
        ]
    },
    install_requires=[
        "arrow ~= 0.14",
        "boto3 ~= 1.9",
        "click ~= 7.0",
        "flask ~= 1.0",
        "mypy-extensions ~= 0.4",
        "pygments ~= 2.4",
        "pyjwt ~= 1.7",
        "requests ~= 2.22",
        "semver ~= 2.8",
        "ruamel.yaml ~= 0.15",
        "typing-extensions ~= 3.7",
        "treelib ~= 1.5",
        "urwid ~= 2.0",
        "werkzeug ~= 0.15",
    ],
    classifiers=[
        "Development Status :: 4 - Beta",
        "License :: OSI Approved :: GNU Affero General Public License v3",
        "License :: OSI Approved :: Apache Software License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3 :: Only",
        "Programming Language :: Python :: Implementation :: CPython",
    ],
)
