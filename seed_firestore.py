"""Seed script for Wanderlust Illustrated destinations and itineraries in Firestore."""

from google.cloud import firestore

PROJECT_ID = "qwiklabs-gcp-03-f717d106da8e"

DESTINATIONS = [
    {
        "id": "kyoto-japan",
        "city": "Kyoto",
        "country": "Japan",
        "vibe": "historical",
        "estimated_daily_budget_usd": 140,
        "description": "Ancient temples, bamboo groves, serene zen gardens, and historic Gion geisha district.",
        "top_attractions": ["Fushimi Inari Shrine", "Kinkaku-ji (Golden Pavilion)", "Arashiyama Bamboo Grove"],
        "best_season": "Spring (Cherry Blossoms) or Autumn (Foliage)",
    },
    {
        "id": "rome-italy",
        "city": "Rome",
        "country": "Italy",
        "vibe": "foodie",
        "estimated_daily_budget_usd": 160,
        "description": "Centuries of open-air history paired with iconic pastas, espresso bars, and lively piazzas.",
        "top_attractions": ["Colosseum", "Vatican Museums & St. Peter's", "Trevi Fountain", "Trastevere Food Walk"],
        "best_season": "April to May or September to October",
    },
    {
        "id": "banff-canada",
        "city": "Banff",
        "country": "Canada",
        "vibe": "nature",
        "estimated_daily_budget_usd": 190,
        "description": "Glacial turquoise lakes, rugged Rocky Mountain peaks, hot springs, and scenic alpine hiking.",
        "top_attractions": ["Lake Louise", "Moraine Lake", "Banff Gondola", "Johnston Canyon"],
        "best_season": "June to August (hiking) or December to March (skiing)",
    },
    {
        "id": "oaxaca-mexico",
        "city": "Oaxaca",
        "country": "Mexico",
        "vibe": "foodie",
        "estimated_daily_budget_usd": 85,
        "description": "Culinary capital with traditional moles, mezcal distilleries, artisan craft markets, and Zapotec ruins.",
        "top_attractions": ["Monte Albán", "Templo de Santo Domingo", "Mercado 20 de Noviembre", "Hierve el Agua"],
        "best_season": "October to March",
    }
]

def seed():
    db = firestore.Client(project=PROJECT_ID)
    print(f"Connecting to Firestore in project {PROJECT_ID}...")
    batch = db.batch()
    
    collection = db.collection("destinations")
    for dest in DESTINATIONS:
        doc_ref = collection.document(dest["id"])
        data = {k: v for k, v in dest.items() if k != "id"}
        batch.set(doc_ref, data)
        print(f"  Queued destination: {dest['city']}, {dest['country']}")
        
    batch.commit()
    print("Firestore seeding complete! Seeded destinations successfully.")

if __name__ == "__main__":
    seed()
