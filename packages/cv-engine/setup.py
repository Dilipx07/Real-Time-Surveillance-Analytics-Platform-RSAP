from pathlib import Path

from setuptools import find_packages, setup


PACKAGE_ROOT = Path(__file__).resolve().parent


setup(
    name="rsap-cv-engine",
    version="1.0.0",
    description="Reusable computer-vision analytics engine for RSAP",
    long_description=(PACKAGE_ROOT / "README.md").read_text(encoding="utf-8"),
    long_description_content_type="text/markdown",
    python_requires=">=3.12",
    packages=find_packages(),
    package_data={"cv_engine": ["py.typed"]},
    install_requires=[
        "numpy>=1.26,<3",
        "scipy>=1.14,<2",
        "opencv-python-headless>=4.10,<5",
        "Pillow>=10,<12",
    ],
    extras_require={
        "yolo": ["ultralytics>=8.2,<9"],
        "face": ["face-recognition>=1.3,<2"],
        "test": ["pytest>=8,<9"],
        "all": ["ultralytics>=8.2,<9", "face-recognition>=1.3,<2"],
    },
)
