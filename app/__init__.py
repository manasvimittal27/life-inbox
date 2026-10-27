"""Load environment variables from a local .env file before any app module reads them."""

from dotenv import load_dotenv

load_dotenv()
