#! /usr/bin/env python
import os
import logging
logger = logging.getLogger('__main__')
logger.info(f'[LogFileLimiter] loading module ')


class LogFileLimiter:
    def __init__(self, path, maxSize):
        """
        Initialize the LogFileLimiter class with the path to the file and the maximum size in kilobytes.
        :param path: Path to the log file.
        :param maxSize: Maximum file size in kilobytes.
        """
        self.path = path
        self.maxSize = maxSize * 1024  # Convert kilobytes to bytes

    def prune(self, pruneFactor):
        """
        Reduces the file size by deleting the earliest lines.
        :param pruneFactor: The fraction of total lines to delete.
        """
        if pruneFactor < 0 or pruneFactor > 1:
            raise ValueError("Prune factor must be between 0 and 1.")

        logger.info(f'[LogFileLimiter] File {self.path} is too large. File will be pruned by {pruneFactor*100:2.0f} %. ')
        with open(self.path, 'r+') as file:
            lines = file.readlines()
            file.seek(0)
            file.truncate()
            file.writelines(lines[int(len(lines) * pruneFactor):])

    def run(self):
        """
        Checks the file size and calls the prune method if necessary.
        """
        fileSize = os.path.getsize(self.path)
        if fileSize > self.maxSize:
            # Determine the prune factor, at least 10%
            pruneFactor = max(0.1, 1 - self.maxSize / fileSize)
            self.prune(pruneFactor)

# Example usage
if __name__ == "__main__":
    limiter = LogFileLimiter("test copy.log", 20)  # Maximum size of 20 KB
    limiter.run()
