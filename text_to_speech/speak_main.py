"""
speak_main.py - the simplest way to run text_to_speak.

Change the two variables below, then run:   python3 speak_main.py
"""

from text_to_speak import text_to_speak


def main():
    text = "Nice work, your timing is getting better. Let's tighten up that left elbow."
    personality = "priyaa"          # priyaa, gordon, ada or bobby

    text_to_speak(text, personality)


if __name__ == "__main__":
    main()
