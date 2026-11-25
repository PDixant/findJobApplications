"""Writes packaging/version_info.txt, the details Windows shows under Properties, Details for
Pathfinder.exe: its name, version, maker and copyright. The spec uses it when it's there.

    python packaging/version_info.py 1.0.0
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main(version):
    nums = [int(n) for n in re.findall(r"\d+", version)[:3]]
    nums += [0] * (4 - len(nums))
    four = tuple(nums[:4])
    text = f"""# Made by packaging/version_info.py; not in git.
VSVersionInfo(
  ffi=FixedFileInfo(filevers={four}, prodvers={four}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Dixant Patel'),
      StringStruct('FileDescription', 'Pathfinder'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', 'Pathfinder'),
      StringStruct('LegalCopyright', 'Copyright (c) 2026 Dixant Patel. MIT license.'),
      StringStruct('OriginalFilename', 'Pathfinder.exe'),
      StringStruct('ProductName', 'Pathfinder'),
      StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    (HERE / "version_info.txt").write_text(text, encoding="utf-8")
    print(f"packaging/version_info.txt: Pathfinder {version}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "0.0.0")
