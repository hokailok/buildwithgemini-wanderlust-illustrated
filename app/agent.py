# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import datetime
import json
import os
from typing import Any, Dict, List, Optional
import urllib.parse
import urllib.request
import uuid
from zoneinfo import ZoneInfo

from a2ui.basic_catalog.provider import BasicCatalog
from a2ui.schema.manager import A2uiSchemaManager
from google import genai
from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.code_executors import AgentEngineSandboxCodeExecutor
from google.adk.models import Gemini
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.cloud import firestore, storage
from google.genai import types

from .a2ui_utils import a2ui_callback

MODEL = "gemini-3.8-flash"
GCP_PROJECT_ID = "qwiklabs-gcp-03-f717d106da8e"
GCS_BUCKET_NAME = f"wanderlust-illustrations-{GCP_PROJECT_ID}"
IMAGE_GEN_MODEL = "gemini-2.5-flash-image"
IMAGE_GEN_LOCATION = "us-central1"
AGENT_ENGINE_ID = "9029918463505727488"
AGENT_ENGINE_LOCATION = "us-east1"
AGENT_ENGINE_RESOURCE_NAME = (
    f"projects/181033323825/locations/{AGENT_ENGINE_LOCATION}/reasoningEngines/{AGENT_ENGINE_ID}"
)
SANDBOX_RESOURCE_NAME = (
    f"projects/181033323825/locations/{AGENT_ENGINE_LOCATION}/reasoningEngines/{AGENT_ENGINE_ID}/sandboxEnvironments/8521756074984865792"
)


async def generate_memories_callback(callback_context: CallbackContext):
    """WRITE: after each turn, send the session events to Memory Bank for extraction."""
    await callback_context.add_session_to_memory()
    return None


def _get_db() -> firestore.Client:
    return firestore.Client(project=GCP_PROJECT_ID)


def list_destinations(vibe: Optional[str] = None) -> List[Dict[str, Any]]:
    """List travel destinations from Firestore, optionally filtered by travel vibe.

    Args:
        vibe: Optional filter for vibe e.g. 'historical', 'foodie', 'nature'.

    Returns:
        A list of destination dictionaries.
    """
    db = _get_db()
    coll = db.collection("destinations")
    if vibe:
        docs = coll.where("vibe", "==", vibe.lower()).stream()
    else:
        docs = coll.stream()

    results = []
    for doc in docs:
        data = doc.to_dict() or {}
        data["id"] = doc.id
        results.append(data)
    return results


def get_destination_details(destination_id: str) -> Dict[str, Any]:
    """Get full details for a specific travel destination from Firestore.

    Args:
        destination_id: The document ID of the destination (e.g. 'kyoto-japan', 'rome-italy').

    Returns:
        The destination details dictionary, or an error message if not found.
    """
    db = _get_db()
    doc = db.collection("destinations").document(destination_id).get()
    if not doc.exists:
        return {"error": f"Destination '{destination_id}' not found in database."}
    data = doc.to_dict() or {}
    data["id"] = doc.id
    return data


def save_itinerary(
    traveler_name: str,
    destination_city: str,
    days: int,
    itinerary_plan: str,
    estimated_budget_usd: Optional[float] = None,
) -> Dict[str, Any]:
    """Save a planned itinerary for a traveler into Firestore.

    Args:
        traveler_name: Name of the traveler or client.
        destination_city: The destination city for the itinerary.
        days: Duration of the trip in days.
        itinerary_plan: Summary or day-by-day plan of the itinerary.
        estimated_budget_usd: Optional total estimated cost in USD.

    Returns:
        Confirmation dictionary with the created itinerary document ID.
    """
    db = _get_db()
    doc_ref = db.collection("itineraries").document()
    record = {
        "traveler_name": traveler_name,
        "destination_city": destination_city,
        "days": days,
        "itinerary_plan": itinerary_plan,
        "estimated_budget_usd": estimated_budget_usd,
        "created_at": firestore.SERVER_TIMESTAMP,
    }
    doc_ref.set(record)
    return {
        "status": "success",
        "itinerary_id": doc_ref.id,
        "message": f"Saved {days}-day itinerary for {traveler_name} to {destination_city}.",
    }


def list_saved_itineraries(traveler_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """List saved itineraries from Firestore, optionally filtered by traveler name.

    Args:
        traveler_name: Optional name of the traveler to filter saved itineraries.

    Returns:
        List of saved itinerary records.
    """
    db = _get_db()
    coll = db.collection("itineraries")
    if traveler_name:
        docs = coll.where("traveler_name", "==", traveler_name).stream()
    else:
        docs = coll.stream()

    results = []
    for doc in docs:
        data = doc.to_dict() or {}
        data["id"] = doc.id
        if "created_at" in data and data["created_at"]:
            data["created_at"] = str(data["created_at"])
        results.append(data)
    return results


def calculate_trip_budget(
    city: str,
    days: int = 3,
    travel_style: str = "moderate",
    num_travelers: int = 1,
) -> Dict[str, Any]:
    """Calculate an estimated trip budget breakdown for a destination.

    Args:
        city: The destination city name (e.g. 'Rome', 'Kyoto', 'Oaxaca', 'Banff').
        days: Trip duration in days (default: 3).
        travel_style: Travel style tier ('backpacker', 'moderate', or 'luxury').
        num_travelers: Number of travelers (default: 1).

    Returns:
        Dictionary with itemized lodging, food, activities, and total estimated budget in USD.
    """
    db = _get_db()
    docs = db.collection("destinations").stream()
    base_daily = 130.0  # sensible fallback if city is unlisted

    for doc in docs:
        d = doc.to_dict() or {}
        if d.get("city", "").lower() == city.lower():
            base_daily = float(d.get("estimated_daily_budget_usd", base_daily))
            break

    multipliers = {"backpacker": 0.65, "moderate": 1.0, "luxury": 2.2}
    mult = multipliers.get(travel_style.lower(), 1.0)
    adjusted_daily_per_person = round(base_daily * mult, 2)

    lodging_per_day = round(adjusted_daily_per_person * 0.45, 2)
    food_per_day = round(adjusted_daily_per_person * 0.30, 2)
    activities_per_day = round(adjusted_daily_per_person * 0.15, 2)
    transit_per_day = round(adjusted_daily_per_person * 0.10, 2)

    total_per_person = round(adjusted_daily_per_person * days, 2)
    total_group = round(total_per_person * num_travelers, 2)

    return {
        "city": city.title(),
        "days": days,
        "travel_style": travel_style.lower(),
        "num_travelers": num_travelers,
        "daily_per_person_usd": adjusted_daily_per_person,
        "breakdown_per_person_per_day": {
            "lodging_usd": lodging_per_day,
            "meals_dining_usd": food_per_day,
            "activities_attractions_usd": activities_per_day,
            "local_transit_usd": transit_per_day,
        },
        "total_estimated_budget_usd": total_group,
    }


def generate_destination_postcard(
    city: str,
    visual_style: str = "vintage travel poster with bold typography and warm colors",
    additional_details: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate a stylized travel postcard illustration for a destination and upload to Cloud Storage.

    Args:
        city: The destination city to illustrate (e.g. 'Kyoto', 'Rome', 'Banff', 'Oaxaca').
        visual_style: Artistic style for the illustration (e.g. 'vintage travel poster', 'watercolor painting', 'retro art deco').
        additional_details: Specific landmarks, seasons, or motifs to include (e.g. 'cherry blossoms and pagoda', 'sunset over colosseum').

    Returns:
        Dictionary containing the public image URL, bucket path, and illustration summary.
    """
    prompt = f"A gorgeous {visual_style} illustration of {city}."
    if additional_details:
        prompt += f" Featuring {additional_details}."
    prompt += " Highly detailed, professional travel poster composition, vibrant colors, postcard aesthetic."

    genai_client = genai.Client(
        vertexai=True,
        project=GCP_PROJECT_ID,
        location=IMAGE_GEN_LOCATION,
    )

    response = genai_client.models.generate_content(
        model=IMAGE_GEN_MODEL,
        contents=prompt,
    )

    image_bytes = None
    mime_type = "image/png"
    if response.candidates and response.candidates[0].content:
        for part in response.candidates[0].content.parts:
            if part.inline_data and part.inline_data.data:
                image_bytes = part.inline_data.data
                if part.inline_data.mime_type:
                    mime_type = part.inline_data.mime_type
                break

    if not image_bytes:
        return {
            "status": "error",
            "message": f"Could not generate postcard illustration for {city}.",
        }

    clean_city = "".join(c.lower() if c.isalnum() else "_" for c in city).strip("_")
    filename = f"postcard_{clean_city}_{uuid.uuid4().hex[:8]}.png"

    storage_client = storage.Client(project=GCP_PROJECT_ID)
    bucket = storage_client.bucket(GCS_BUCKET_NAME)
    blob = bucket.blob(filename)
    blob.upload_from_string(image_bytes, content_type=mime_type)

    public_url = f"https://storage.googleapis.com/{GCS_BUCKET_NAME}/{filename}"

    return {
        "status": "success",
        "city": city.title(),
        "prompt": prompt,
        "image_url": public_url,
        "gcs_uri": f"gs://{GCS_BUCKET_NAME}/{filename}",
        "message": f"Postcard illustration for {city} generated and published successfully.",
    }


def get_destination_weather(city: str) -> Dict[str, Any]:
    """Fetch live weather conditions and forecast for a travel destination using wttr.in.

    Args:
        city: The destination city to get current weather and packing insights for (e.g. 'Kyoto', 'Rome', 'Banff', 'Oaxaca').

    Returns:
        Dictionary with live temperatures, conditions, humidity, and forecast highs/lows.
    """
    clean_city = urllib.parse.quote(city.strip())
    url = f"https://wttr.in/{clean_city}?format=j1"
    req = urllib.request.Request(url, headers={"User-Agent": "curl/7.68.0"})

    try:
        with urllib.request.urlopen(req, timeout=8) as response:
            data = json.loads(response.read().decode("utf-8"))

        current = data.get("current_condition", [{}])[0]
        weather_days = data.get("weather", [])
        today = weather_days[0] if weather_days else {}

        temp_c = current.get("temp_C", "N/A")
        temp_f = current.get("temp_F", "N/A")
        feels_like_c = current.get("FeelsLikeC", "N/A")
        feels_like_f = current.get("FeelsLikeF", "N/A")
        conditions = current.get("weatherDesc", [{}])[0].get("value", "Clear").strip()
        humidity = current.get("humidity", "N/A")
        wind_speed_kmph = current.get("windspeedKmph", "N/A")

        high_c = today.get("maxtempC", "N/A")
        high_f = today.get("maxtempF", "N/A")
        low_c = today.get("mintempC", "N/A")
        low_f = today.get("mintempF", "N/A")

        return {
            "city": city.title(),
            "current_temperature": f"{temp_c}°C ({temp_f}°F)",
            "feels_like": f"{feels_like_c}°C ({feels_like_f}°F)",
            "conditions": conditions,
            "humidity": f"{humidity}%",
            "wind_speed": f"{wind_speed_kmph} km/h",
            "forecast_high": f"{high_c}°C ({high_f}°F)",
            "forecast_low": f"{low_c}°C ({low_f}°F)",
        }
    except Exception as e:
        return {
            "city": city.title(),
            "error": f"Could not retrieve live weather for {city}: {str(e)}",
        }


def geocode_address(address: str) -> Dict[str, Any]:
    """Geocode an address, city, or landmark into latitude and longitude coordinates using Google Maps Geocoding API.

    Args:
        address: The address or place to geocode (e.g. 'Colosseum, Rome', 'Kyoto Station', 'Banff Avenue').

    Returns:
        Dictionary containing formatted address, latitude, and longitude.
    """
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    if not api_key or api_key == "PASTE_KEY_HERE":
        return {
            "error": "GOOGLE_MAPS_API_KEY is not set or still has the placeholder in .env. Please configure your Google Maps API key."
        }

    query = urllib.parse.quote(address)
    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={query}&key={api_key}"
    req = urllib.request.Request(url)

    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))

        status = data.get("status")
        if status != "OK" or not data.get("results"):
            error_msg = data.get("error_message", f"Geocoding failed with status: {status}")
            return {"error": error_msg}

        result = data["results"][0]
        location = result.get("geometry", {}).get("location", {})
        return {
            "query": address,
            "formatted_address": result.get("formatted_address"),
            "latitude": location.get("lat"),
            "longitude": location.get("lng"),
            "place_id": result.get("place_id"),
        }
    except Exception as e:
        return {"error": f"Geocoding request failed: {str(e)}"}


def find_nearby_places(
    latitude: float,
    longitude: float,
    place_type: str = "tourist_attraction",
    radius_meters: float = 1500.0,
    max_results: int = 5,
) -> Dict[str, Any]:
    """Find nearby places of interest around specific coordinates using Google Places API (New).

    Args:
        latitude: Latitude coordinate of the search center.
        longitude: Longitude coordinate of the search center.
        place_type: Type of place to search for (e.g. 'restaurant', 'cafe', 'museum', 'tourist_attraction', 'park', 'historical_landmark').
        radius_meters: Search radius in meters (0 to 50000, default: 1500).
        max_results: Maximum number of places to return (1 to 20, default: 5).

    Returns:
        Dictionary with a list of nearby places including name, address, and coordinates.
    """
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    if not api_key or api_key == "PASTE_KEY_HERE":
        return {
            "error": "GOOGLE_MAPS_API_KEY is not set or still has the placeholder in .env. Please configure your Google Maps API key."
        }

    url = "https://places.googleapis.com/v1/places:searchNearby"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location,places.types,places.rating",
    }
    payload = {
        "includedTypes": [place_type.lower()],
        "maxResultCount": min(max(1, max_results), 20),
        "locationRestriction": {
            "circle": {
                "center": {"latitude": latitude, "longitude": longitude},
                "radius": float(radius_meters),
            }
        },
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))

        places_found = []
        for p in data.get("places", []):
            loc = p.get("location", {})
            name = p.get("displayName", {}).get("text", "Unknown")
            places_found.append({
                "name": name,
                "address": p.get("formattedAddress"),
                "latitude": loc.get("latitude"),
                "longitude": loc.get("longitude"),
                "rating": p.get("rating"),
                "types": p.get("types", []),
            })

        return {
            "center": {"latitude": latitude, "longitude": longitude},
            "place_type": place_type,
            "places_count": len(places_found),
            "places": places_found,
        }
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else ""
        return {"error": f"Places API HTTP error {e.code}: {error_body}"}
    except Exception as e:
        return {"error": f"Places API request failed: {str(e)}"}


schema_manager = A2uiSchemaManager(
    version="0.8",
    catalogs=[BasicCatalog.get_config("0.8")],
)

instruction = schema_manager.generate_system_prompt(
    role_description=(
        "You are Wanderlust Illustrated, a specialized travel planner assistant. "
        "You remember the user's travel preferences, dietary restrictions, favorite vibes, "
        "and past conversation details across sessions, using them to personalize recommendations. "
        "You help travelers explore curated destinations, plan personalized multi-day itineraries, "
        "check live destination weather, estimate travel budgets, create custom destination postcards, "
        "and find real locations, addresses, and nearby places using Google Maps Geocoding and Places tools. "
        "You use database tools to query destinations and save itineraries, live weather tools to check forecasts, "
        "Maps tools to locate attractions and dining, generation tools to illustrate travel highlights, "
        "and sandboxed Python code execution to calculate precise budget formulas or data conversions."
    ),
    workflow_description="Analyze the request, call tools when needed, and return structured UI when appropriate.",
    ui_description=(
        "Keep every surface tiny and flat: ONE Card > ONE Column > a few Text rows. "
        "Never nest a Card inside a Card. "
        "Use ONLY these components: Card, Column, Row, Text, and Image. Do not use "
        "Table or Heading (unsupported), or Buttons, actions, or forms (they do "
        "nothing in adk web). "
        "You may include one Image component, but only when you have a public https "
        "URL for the image (for example the URL an image tool returns after uploading "
        "to a public bucket). Set the Image url to that exact https link, for example "
        "{\"Image\": {\"url\": {\"literalString\": \"https://...\"}}}. Never point an "
        "Image at a bare filename, an artifact name, or a non-http(s) path. If you do "
        "not have a public URL, add a short Text line noting the image instead. "
        "No markdown in text; use the usageHint property ('h1', 'h2', 'body') for "
        "headings and emphasis. "
        "Output ONLY the raw A2UI JSON array — no prose, and never wrap it in "
        "<a2a_datapart_json> tags or 'kind'/'data'/'metadata' objects."
    ),
    include_schema=True,
    include_examples=True,
)


root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model=MODEL,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=instruction,
    code_executor=AgentEngineSandboxCodeExecutor(
        sandbox_resource_name=SANDBOX_RESOURCE_NAME,
        agent_engine_resource_name=AGENT_ENGINE_RESOURCE_NAME,
    ),
    tools=[
        PreloadMemoryTool(),
        list_destinations,
        get_destination_details,
        get_destination_weather,
        geocode_address,
        find_nearby_places,
        calculate_trip_budget,
        generate_destination_postcard,
        save_itinerary,
        list_saved_itineraries,
    ],
    after_model_callback=a2ui_callback,
    after_agent_callback=generate_memories_callback,
)

app = App(
    root_agent=root_agent,
    name="app",
)
