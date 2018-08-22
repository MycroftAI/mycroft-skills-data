# Generator for skill-metadata.json

This tool harvests information from all Skills in the the
[Mycroft Skills](https://github.com/MycroftAI/my) repo for release 18.02.

Usage:  ```python3 harvest.py [-o filename] [-u]```
where
        -o  output to a JSON file instead of screen
        -u  upload results to the mycroft-skills-data repo
            (requires appropriate permissions)