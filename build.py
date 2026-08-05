# -*- coding: utf-8 -*-

import argparse
import datetime
import os
import subprocess
import sys


FILE_VERSION_RESOURCE = r"""# UTF-8
#
# For more details about fixed file info 'ffi' see:
# http://msdn.microsoft.com/en-us/library/ms646997.aspx
VSVersionInfo(
  ffi=FixedFileInfo(
    # filevers and prodvers should be always a tuple with four items: (1, 2, 3, 4)
    # Set not needed items to zero 0.
    filevers=(%(main_ver)d, %(sub_ver)d, %(min_ver)d, %(build_num)d),
    prodvers=(%(main_ver)d, %(sub_ver)d, %(min_ver)d, %(build_num)d),
    # Contains a bitmask that specifies the valid bits 'flags'r
    mask=0x17,
    # Contains a bitmask that specifies the Boolean attributes of the file.
    flags=0x0,
    # The operating system for which this file was designed.
    # 0x4 - NT and there is no need to change it.
    OS=0x4,
    # The general type of file.
    # 0x1 - the file is an application.
    fileType=0x1,
    # The function of the file.
    # 0x0 - the function is not defined for this fileType
    subtype=0x0,
    # Creation date and time stamp.
    date=(0, 0)
    ),
  kids=[
    StringFileInfo(
      [
      StringTable(
        u'080404b0',
        [StringStruct(u'CompanyName', u'drunkdream.com'),
        StringStruct(u'FileDescription', u'weread-exporter'),
        StringStruct(u'FileVersion', u'%(main_ver)d.%(sub_ver)d.%(min_ver)d'),
        StringStruct(u'InternalName', u'weread-exporter.exe'),
        StringStruct(u'LegalCopyright', u'Copyright (C) 2017-%(year)d drunkdream.com. All Rights Reserved'),
        StringStruct(u'OriginalFilename', u'weread-exporter.exe'),
        StringStruct(u'ProductName', u'weread-exporter'),
        StringStruct(u'ProductVersion', u'%(main_ver)d.%(sub_ver)d.%(min_ver)d')])
      ]), 
    VarFileInfo([VarStruct(u'Translation', [2052, 1200])])
  ]
)
"""


def build_by_pyinstaller(platform, version):
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "pyinstaller"],
        check=True,
    )
    version_items = version.split(".")
    for i in range(len(version_items)):
        version_items[i] = int(version_items[i])

    with open("version.py", "w", encoding="utf-8") as fp:
        fp.write('version_info=u"%s"' % version)

    main_file = "gui.py"
    data_separator = ";" if os.name == "nt" else ":"
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",
        "--windowed",
        main_file,
        "--name",
        "WeReadExporterGUI",
    ]

    if sys.platform == "win32":
        version_file = "version_file.txt"
        text = FILE_VERSION_RESOURCE % {
            "main_ver": version_items[0],
            "sub_ver": version_items[1],
            "min_ver": version_items[2],
            "build_num": version_items[3] if len(version_items) > 3 else 0,
            "year": datetime.datetime.today().year,
        }
        with open(version_file, "w", encoding="utf-8") as fp:
            fp.write(text)
        cmd += ["--version-file", version_file]

    for filename in ("hook.js", "style.css", "epub.css"):
        source = os.path.join("weread_exporter", filename)
        cmd += ["--add-data", source + data_separator + "weread_exporter"]

    icon_source = os.path.join("assets", "weread_exporter.ico")
    if os.path.isfile(icon_source):
        cmd += [
            "--icon",
            icon_source,
            "--add-data",
            icon_source + data_separator + "assets",
        ]

    bin_dir = os.path.join("weread_exporter", "bin", platform)
    if os.path.isdir(bin_dir):
        cmd += [
            "--add-data",
            bin_dir + data_separator + os.path.join("weread_exporter", "bin", platform),
        ]

    subprocess.run(cmd, check=True)


def build(backend, version):
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"],
        check=True,
    )

    platform = "win32"
    if sys.platform.startswith("linux"):
        platform = "linux"
    elif sys.platform == "darwin":
        platform = "macos"

    if backend == "pyinstaller":
        return build_by_pyinstaller(platform, version)
    else:
        raise NotImplementedError(backend)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog="build-weread-exporter", description="Build weread-exporter tool."
    )
    parser.add_argument(
        "--backend",
        help="build backend",
        choices=("pyinstaller", "py2exe"),
        default="pyinstaller",
    )
    parser.add_argument("--version", help="version(1.2.3)", default="1.0.0")
    args = parser.parse_args()

    sys.exit(build(args.backend, args.version))
