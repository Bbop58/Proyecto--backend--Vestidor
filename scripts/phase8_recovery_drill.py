"""Historical pre-SaaS drill: deliberately refuses to apply obsolete privileges."""
import sys

def main():
    print('Historical phase 8 drill is incompatible with the dedicated S1 platform identity. '
          'Use scripts/saas_s6_recovery_drill.py --run-local-disposable against its fixed empty local QA databases. '
          'No database was contacted.', file=sys.stderr)
    return 2

if __name__=='__main__':sys.exit(main())
