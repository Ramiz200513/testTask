import sys

from .cli import main

if __name__ == "__main__":
    # Если вывод перенаправлен в файл или канал с однобайтовой кодировкой,
    # не падать на редком символе, а заменить его.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    sys.exit(main())
