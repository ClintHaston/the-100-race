"""Runs the full tick program over past prices, one step at a time, as if it were live. No network, no news."""
import os, sys, time, json, shutil, importlib
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
bars, out_dir, start, days, step = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5])
shutil.rmtree(out_dir, ignore_errors=True)
os.environ.update(RACE_REPLAY=bars, RACE_DATA=out_dir)
t0 = datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp()
import race.bot as bot
n = days * 86400 // step
tick = time.time()
for i in range(n + 1):
    os.environ["RACE_NOW"] = str(t0 + i * step)
    if i % 200 == 0 or i == n:
        print(datetime.fromtimestamp(t0 + i * step, timezone.utc).strftime("%b %d %H:%M"), end=" ")
        bot.main()
    else:
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()):
            bot.main()
print(f"{n + 1} ticks in {time.time() - tick:.0f}s")
