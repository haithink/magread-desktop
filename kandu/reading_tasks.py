"""Serialize local accounting writes and dashboard reads off the Tk thread."""
from concurrent.futures import ThreadPoolExecutor

WRITER = ThreadPoolExecutor(max_workers=1, thread_name_prefix='reading-data')
