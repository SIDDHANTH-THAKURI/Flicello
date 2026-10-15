from .cli import app

if __name__ == "__main__":  # also keeps worker processes (multiprocessing re-imports the main module) from running the CLI
    app()
