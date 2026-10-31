import os
from dotenv import load_dotenv


def main():
    load_dotenv()
    print("Python environment setup completed successfully!")
    print(f"Working Directory: {os.getcwd()}")


if __name__ == "__main__":
    main()
