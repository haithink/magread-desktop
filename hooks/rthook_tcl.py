import os, sys
base = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
os.environ.setdefault('TCL_LIBRARY', os.path.join(base, 'tcl8.6'))
os.environ.setdefault('TK_LIBRARY', os.path.join(base, 'tk8.6'))
