import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))


def write(root: Path, files: dict[str, str]) -> None:
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body).lstrip("\n"), encoding="utf-8")


@pytest.fixture
def project(tmp_path):
    """A small multi-file Python project with cross-file calls, inheritance, a decorator and a broken file."""
    (tmp_path / ".claude" / "atlas").mkdir(parents=True)
    write(tmp_path, {
        "pkg/__init__.py": "from .util import clamp\n",
        "pkg/util.py": '''
            def clamp(x, lo, hi):
                """Limit x to the range [lo, hi]."""
                return max(lo, min(x, hi))

            def unused_helper():
                return 1
        ''',
        "pkg/shapes.py": '''
            from pkg import clamp

            class Shape:
                def area(self):
                    return 0

                def describe(self):
                    return f"area={self.area()}"

            class Circle(Shape):
                def __init__(self, r: float):
                    self.r = r

                def area(self):
                    return clamp(3.14 * self.r * self.r, 0, 1000)

            def make_circle(r) -> Circle:
                return Circle(r)
        ''',
        "app.py": '''
            import pkg.util
            from pkg.shapes import Circle, make_circle

            def register(fn):
                return fn

            @register
            def handler(event):
                c = make_circle(2)
                total = c.area() + pkg.util.clamp(5, 0, 3)
                return report(c, total)

            def report(shape: Circle, total):
                print(shape.describe(), total)

            def main():
                handler({"a": 1})

            if __name__ == "__main__":
                main()
        ''',
        "day-1.py": "class Solution:\n    def twoSum(self, nums, target):\n",
    })
    return tmp_path
