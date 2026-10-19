"""A runnable tour of all four components.  py -m components.example"""
from datetime import date, datetime, timedelta

from . import alerting, credibility, emergency, handle_message, health_check, intake

print("health check")
for k, v in health_check().items():
    print(f"   {k:<26} {v}")

print("\n1. the whole chain, one message at a time")
for msg in ["the drain outside my house on nostrand ave is flooding",
            "I smell gas in my building",
            "no heat in my apartment for 3 days and i have a baby",
            "can i get a copy of my birth certificate"]:
    r = handle_message(msg, citizen_id="CIT-000014")
    print(f"   {msg[:48]:<50} -> {r}")

print("\n2. why it decided that")
r = handle_message("the drain on my corner is blocked and flooding the crosswalk", citizen_id="CIT-01")
for line in r.explain():
    print("   -", line)

print("\n3. the safeguard on its own")
for t in ["someone collapsed in the lobby and is not moving",
          "the fire escape on my building is rusted",
          "there is a pothole on my street"]:
    s = emergency.screen(t)
    print(f"   {s.action:<22} {s.category:<22} {t[:44]}")

print("\n4. credibility, before and after a crew confirms a report")
now = datetime.now()
hist = [credibility.Observation("GENUINE", now - timedelta(days=d), "RT001") for d in (20, 60, 120)]
before = credibility.assess("CIT-7", history=hist)
print(f"   score {before.credibility_score} ({before.credibility_band}) after {before.observation_count} reports")
out = credibility.verify_outcome("VALID_RESOLVED", ["CIT-7", "CIT-9"], {"CIT-7": hist})
print(f"   crew said fixed -> {out.verified_outcome}, moved {out.credibility_deltas}")

print("\n5. tomorrow's demand, with 38 mm of rain forecast")
f = alerting.forecast("BROOKLYN", date.today(), {"wx_precip_mm": 38.0, "wx_snow_cm": 0.0,
                                                 "wx_tmax_c": 17.0, "wx_tmin_c": 11.0, "wx_gust_kmh": 45.0})
for a in f.alerts[:4]:
    print("  ", a)
if not f.alerts:
    print("   nothing unusual expected")
