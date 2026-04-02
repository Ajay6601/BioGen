from setuptools import setup, find_packages

setup(
    name="biogen",
    version="0.1.0",
    packages=find_packages(),
    python_requires=">=3.10",
    entry_points={
        "console_scripts": [
            "biogen=biogen.main:cli",
        ],
    },
)
