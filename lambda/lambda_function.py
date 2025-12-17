# -*- coding: utf-8 -*-
"""
TBM Horaires - Alexa Skill
Horaires temps réel des transports TBM Bordeaux Métropole
"""

import logging
import os
from datetime import datetime, timezone
from typing import Optional

from ask_sdk_core.skill_builder import CustomSkillBuilder
from ask_sdk_core.dispatch_components import (
    AbstractRequestHandler,
    AbstractExceptionHandler,
)
from ask_sdk_core.utils import is_request_type, is_intent_name, get_slot_value
from ask_sdk_core.handler_input import HandlerInput
from ask_sdk_model import Response
from ask_sdk_dynamodb.adapter import DynamoDbAdapter

from api import TBMClient

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# DynamoDB for persistence (optional - uses session if not available)
ddb_region = os.environ.get("DYNAMODB_REGION", "eu-west-1")
ddb_table = os.environ.get("DYNAMODB_TABLE", "tbm-horaires-users")

try:
    from ask_sdk_dynamodb.adapter import DynamoDbAdapter
    import boto3
    ddb_resource = boto3.resource("dynamodb", region_name=ddb_region)
    dynamodb_adapter = DynamoDbAdapter(table_name=ddb_table, create_table=False, dynamodb_resource=ddb_resource)
except Exception:
    dynamodb_adapter = None

# Initialize API client
tbm_client = TBMClient()

# Default stop configuration (can be changed by user)
DEFAULT_CONFIG = {
    "stop_name": "Quarante Journaux",
    "line_name": "Tram C",
    "dest_name": "Les Pyrénées",
}


def _mins_to(iso_ts: Optional[str]) -> Optional[int]:
    """Convert ISO timestamp to minutes from now."""
    if not iso_ts:
        return None
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        return max(0, int((dt - datetime.now(timezone.utc)).total_seconds() // 60))
    except Exception:
        return None


def get_persistent_attributes(handler_input: HandlerInput) -> dict:
    """Get user preferences from persistence or session."""
    try:
        attrs = handler_input.attributes_manager.persistent_attributes
        if attrs:
            return attrs
    except Exception:
        pass
    
    session_attrs = handler_input.attributes_manager.session_attributes or {}
    if session_attrs:
        return session_attrs
    
    return {}


def get_config_or_default(handler_input: HandlerInput) -> dict:
    """Get user config or initialize with default if none exists."""
    attrs = get_persistent_attributes(handler_input)
    
    # If no config saved, search and cache the default
    if not attrs.get("stop_point_ref"):
        # Search for default stop
        search_results = tbm_client.search_stop(
            stop_query=DEFAULT_CONFIG["stop_name"],
            line_query=DEFAULT_CONFIG["line_name"],
            dest_query=DEFAULT_CONFIG["dest_name"]
        )
        if search_results:
            result = search_results[0]
            attrs = {
                "stop_point_ref": result.get("stop_point_ref"),
                "stop_name": result.get("stop_name") or DEFAULT_CONFIG["stop_name"],
                "line_ref": result.get("line_ref"),
                "line_name": result.get("line_name") or DEFAULT_CONFIG["line_name"],
                "direction_ref": result.get("direction_ref"),
                "dest_name": result.get("dest_name") or DEFAULT_CONFIG["dest_name"],
                "is_default": True,
            }
    
    return attrs


def save_persistent_attributes(handler_input: HandlerInput, attrs: dict):
    """Save user preferences."""
    try:
        handler_input.attributes_manager.persistent_attributes = attrs
        handler_input.attributes_manager.save_persistent_attributes()
    except Exception:
        handler_input.attributes_manager.session_attributes = attrs


def get_session_attributes(handler_input: HandlerInput) -> dict:
    """Get session attributes."""
    return handler_input.attributes_manager.session_attributes or {}


def set_session_attributes(handler_input: HandlerInput, attrs: dict):
    """Set session attributes."""
    handler_input.attributes_manager.session_attributes = attrs


class LaunchRequestHandler(AbstractRequestHandler):
    """Handler for Skill Launch."""

    def can_handle(self, handler_input: HandlerInput) -> bool:
        return is_request_type("LaunchRequest")(handler_input)

    def handle(self, handler_input: HandlerInput) -> Response:
        attrs = get_config_or_default(handler_input)
        
        stop_name = attrs.get("stop_name", "Quarante Journaux")
        line_name = attrs.get("line_name", "Tram C")
        is_default = attrs.get("is_default", False)
        
        if is_default:
            speech = f"Bienvenue. Arrêt par défaut : {stop_name}, {line_name}. "
            speech += "Dites 'prochain passage' ou 'changer d'arrêt'."
        else:
            speech = f"Bienvenue. Votre arrêt : {stop_name}, {line_name}. "
            speech += "Dites 'prochain passage' ou 'changer d'arrêt'."

        return (
            handler_input.response_builder
            .speak(speech)
            .ask("Que souhaitez-vous faire ?")
            .response
        )


class GetNextDeparturesIntentHandler(AbstractRequestHandler):
    """Handler for getting next departures."""

    def can_handle(self, handler_input: HandlerInput) -> bool:
        return is_intent_name("GetNextDeparturesIntent")(handler_input)

    def handle(self, handler_input: HandlerInput) -> Response:
        attrs = get_config_or_default(handler_input)
        slot_line = get_slot_value(handler_input, "lineName")
        
        stop_point_ref = attrs.get("stop_point_ref")
        line_ref = attrs.get("line_ref")
        direction_ref = attrs.get("direction_ref", -1)
        stop_name = attrs.get("stop_name")
        line_name = attrs.get("line_name")
        dest_name = attrs.get("dest_name")

        # If user specified a line, update it
        if slot_line:
            line_info = tbm_client.find_line_by_query(slot_line)
            if line_info:
                line_ref = line_info.get("line_ref")
                line_name = line_info.get("line_name")

        if not stop_point_ref:
            # This shouldn't happen with default config, but just in case
            speech = "Erreur de configuration. Dites 'enregistre l'arrêt' pour configurer."
            return (
                handler_input.response_builder
                .speak(speech)
                .ask("Quel arrêt ?")
                .response
            )

        # Fetch departures from API
        try:
            departures = tbm_client.get_departures(
                stop_point_ref=stop_point_ref,
                line_ref=line_ref,
                direction_ref=direction_ref
            )
        except Exception as e:
            logger.error(f"API error: {e}")
            speech = "Désolé, je n'ai pas pu récupérer les horaires. Réessayez dans quelques instants."
            return handler_input.response_builder.speak(speech).response

        if not departures:
            speech = f"Pas de passage prévu pour {line_name or 'cette ligne'} à {stop_name}."
            return handler_input.response_builder.speak(speech).response

        # Build response
        line_label = line_name or "Le prochain"
        dest_label = f" direction {dest_name}" if dest_name else ""
        
        if len(departures) == 1:
            mins = _mins_to(departures[0].get("expected") or departures[0].get("aimed"))
            if mins == 0:
                speech = f"{line_label}{dest_label} arrive maintenant."
            elif mins == 1:
                speech = f"{line_label}{dest_label} dans 1 minute."
            else:
                speech = f"{line_label}{dest_label} dans {mins} minutes."
        else:
            times = []
            for dep in departures[:3]:
                mins = _mins_to(dep.get("expected") or dep.get("aimed"))
                if mins == 0:
                    times.append("maintenant")
                elif mins == 1:
                    times.append("1 minute")
                else:
                    times.append(f"{mins} minutes")
            
            times_str = ", ".join(times[:-1]) + f" et {times[-1]}" if len(times) > 1 else times[0]
            speech = f"{line_label}{dest_label} : dans {times_str}."

        return handler_input.response_builder.speak(speech).response


class SetFavoriteStopIntentHandler(AbstractRequestHandler):
    """Handler for setting favorite stop - Step 1: Get stop name."""

    def can_handle(self, handler_input: HandlerInput) -> bool:
        return is_intent_name("SetFavoriteStopIntent")(handler_input)

    def handle(self, handler_input: HandlerInput) -> Response:
        slot_stop = get_slot_value(handler_input, "stopName")

        if not slot_stop:
            speech = "Je n'ai pas compris le nom de l'arrêt. Pouvez-vous répéter ?"
            return (
                handler_input.response_builder
                .speak(speech)
                .ask("Quel est le nom de l'arrêt ?")
                .response
            )

        # Save stop name in session for multi-turn
        session = get_session_attributes(handler_input)
        session["pending_stop_name"] = slot_stop
        set_session_attributes(handler_input, session)

        speech = f"D'accord, l'arrêt {slot_stop}. Quelle ligne ? Par exemple 'tram C' ou 'liane 1'."
        return (
            handler_input.response_builder
            .speak(speech)
            .ask("Quelle ligne ?")
            .response
        )


class SetFavoriteLineIntentHandler(AbstractRequestHandler):
    """Handler for setting favorite line - Step 2: Get line."""

    def can_handle(self, handler_input: HandlerInput) -> bool:
        return is_intent_name("SetFavoriteLineIntent")(handler_input)

    def handle(self, handler_input: HandlerInput) -> Response:
        slot_line = get_slot_value(handler_input, "lineName")
        session = get_session_attributes(handler_input)

        if not slot_line:
            speech = "Je n'ai pas compris la ligne. Dites par exemple 'tram C' ou 'liane 1'."
            return (
                handler_input.response_builder
                .speak(speech)
                .ask("Quelle ligne ?")
                .response
            )

        # Save line in session
        session["pending_line_name"] = slot_line
        set_session_attributes(handler_input, session)

        # Get line info to show available directions
        line_info = tbm_client.find_line_by_query(slot_line)
        
        if line_info:
            dest = line_info.get("dest_name", "")
            speech = f"Ligne {slot_line}. Quelle direction ? Par exemple 'direction {dest}' ou dites 'n'importe'."
        else:
            speech = f"Ligne {slot_line}. Quelle direction ?"

        return (
            handler_input.response_builder
            .speak(speech)
            .ask("Quelle direction ?")
            .response
        )


class SetFavoriteDirectionIntentHandler(AbstractRequestHandler):
    """Handler for setting favorite direction - Step 3: Complete setup."""

    def can_handle(self, handler_input: HandlerInput) -> bool:
        return is_intent_name("SetFavoriteDirectionIntent")(handler_input)

    def handle(self, handler_input: HandlerInput) -> Response:
        slot_dest = get_slot_value(handler_input, "destinationName")
        session = get_session_attributes(handler_input)
        
        pending_stop = session.get("pending_stop_name")
        pending_line = session.get("pending_line_name")

        if not pending_stop or not pending_line:
            speech = "Je n'ai pas toutes les informations. Recommencez en disant 'enregistre l'arrêt' suivi du nom."
            return (
                handler_input.response_builder
                .speak(speech)
                .ask("Quel arrêt souhaitez-vous enregistrer ?")
                .response
            )

        # Search for the complete combination
        search_results = tbm_client.search_stop(
            stop_query=pending_stop,
            line_query=pending_line,
            dest_query=slot_dest
        )

        if not search_results:
            speech = f"Je n'ai pas trouvé l'arrêt {pending_stop} pour la ligne {pending_line}. Essayez avec un autre nom."
            return (
                handler_input.response_builder
                .speak(speech)
                .ask("Quel arrêt cherchez-vous ?")
                .response
            )

        # Use the first match
        result = search_results[0]
        
        # Save to persistence
        attrs = {
            "stop_point_ref": result.get("stop_point_ref"),
            "stop_name": result.get("stop_name") or pending_stop,
            "line_ref": result.get("line_ref"),
            "line_name": result.get("line_name") or pending_line,
            "direction_ref": result.get("direction_ref"),
            "dest_name": result.get("dest_name") or slot_dest,
        }
        save_persistent_attributes(handler_input, attrs)

        # Clear session
        session.pop("pending_stop_name", None)
        session.pop("pending_line_name", None)
        set_session_attributes(handler_input, session)

        stop_name = attrs.get("stop_name")
        line_name = attrs.get("line_name")
        dest_name = attrs.get("dest_name")

        speech = f"Parfait ! Arrêt {stop_name}, ligne {line_name}"
        if dest_name:
            speech += f" direction {dest_name}"
        speech += ". Dites 'prochain passage' pour les horaires."

        return (
            handler_input.response_builder
            .speak(speech)
            .ask("Voulez-vous les prochains passages ?")
            .response
        )


class GetFavoriteIntentHandler(AbstractRequestHandler):
    """Handler for getting current favorite stop."""

    def can_handle(self, handler_input: HandlerInput) -> bool:
        return is_intent_name("GetFavoriteIntent")(handler_input)

    def handle(self, handler_input: HandlerInput) -> Response:
        attrs = get_config_or_default(handler_input)
        
        stop_name = attrs.get("stop_name", "Quarante Journaux")
        line_name = attrs.get("line_name", "Tram C")
        dest_name = attrs.get("dest_name", "Les Pyrénées")
        is_default = attrs.get("is_default", False)

        if is_default:
            speech = f"Arrêt par défaut : {stop_name}, {line_name}"
        else:
            speech = f"Votre arrêt : {stop_name}, {line_name}"
        
        if dest_name:
            speech += f" direction {dest_name}"
        speech += ". Dites 'changer d'arrêt' pour modifier."

        return handler_input.response_builder.speak(speech).response


class ClearFavoriteIntentHandler(AbstractRequestHandler):
    """Handler for clearing favorite stop."""

    def can_handle(self, handler_input: HandlerInput) -> bool:
        return is_intent_name("ClearFavoriteIntent")(handler_input)

    def handle(self, handler_input: HandlerInput) -> Response:
        save_persistent_attributes(handler_input, {})
        speech = "J'ai supprimé votre arrêt. Dites 'enregistre l'arrêt' pour en configurer un nouveau."
        return handler_input.response_builder.speak(speech).ask("Quel arrêt ?").response


class ChangeStopIntentHandler(AbstractRequestHandler):
    """Handler for changing stop - prompts user to enter new stop."""

    def can_handle(self, handler_input: HandlerInput) -> bool:
        return is_intent_name("ChangeStopIntent")(handler_input)

    def handle(self, handler_input: HandlerInput) -> Response:
        speech = "D'accord. Quel arrêt ? Dites 'enregistre l'arrêt' suivi du nom."
        return handler_input.response_builder.speak(speech).ask("Quel arrêt ?").response


class ListLinesIntentHandler(AbstractRequestHandler):
    """Handler for listing available lines."""

    def can_handle(self, handler_input: HandlerInput) -> bool:
        return is_intent_name("ListLinesIntent")(handler_input)

    def handle(self, handler_input: HandlerInput) -> Response:
        speech = (
            "Les lignes TBM : trams A, B, C, D. "
            "Bus Lianes 1 à 16. Et le Batcub. "
            "Pour configurer, dites 'enregistre l'arrêt' suivi du nom."
        )
        return handler_input.response_builder.speak(speech).ask("Quelle ligne ?").response


class HelpIntentHandler(AbstractRequestHandler):
    """Handler for Help Intent."""

    def can_handle(self, handler_input: HandlerInput) -> bool:
        return is_intent_name("AMAZON.HelpIntent")(handler_input)

    def handle(self, handler_input: HandlerInput) -> Response:
        speech = (
            "Je donne les horaires TBM en temps réel. "
            "Dites 'enregistre l'arrêt' suivi du nom pour configurer. "
            "Puis 'prochain passage' pour les horaires."
        )
        return handler_input.response_builder.speak(speech).ask("Que faire ?").response


class CancelOrStopIntentHandler(AbstractRequestHandler):
    """Handler for Cancel and Stop Intents."""

    def can_handle(self, handler_input: HandlerInput) -> bool:
        return (
            is_intent_name("AMAZON.CancelIntent")(handler_input)
            or is_intent_name("AMAZON.StopIntent")(handler_input)
        )

    def handle(self, handler_input: HandlerInput) -> Response:
        speech = "À bientôt !"
        return handler_input.response_builder.speak(speech).response


class FallbackIntentHandler(AbstractRequestHandler):
    """Handler for Fallback Intent."""

    def can_handle(self, handler_input: HandlerInput) -> bool:
        return is_intent_name("AMAZON.FallbackIntent")(handler_input)

    def handle(self, handler_input: HandlerInput) -> Response:
        speech = "Je n'ai pas compris. Dites 'prochain passage' ou 'aide'."
        return handler_input.response_builder.speak(speech).ask("Que faire ?").response


class SessionEndedRequestHandler(AbstractRequestHandler):
    """Handler for Session End."""

    def can_handle(self, handler_input: HandlerInput) -> bool:
        return is_request_type("SessionEndedRequest")(handler_input)

    def handle(self, handler_input: HandlerInput) -> Response:
        return handler_input.response_builder.response


class CatchAllExceptionHandler(AbstractExceptionHandler):
    """Catch all exception handler."""

    def can_handle(self, handler_input: HandlerInput, exception: Exception) -> bool:
        return True

    def handle(self, handler_input: HandlerInput, exception: Exception) -> Response:
        logger.error(f"Exception: {exception}", exc_info=True)
        speech = "Désolé, une erreur s'est produite. Réessayez."
        return handler_input.response_builder.speak(speech).ask("Que faire ?").response


# Skill Builder
sb = CustomSkillBuilder(persistence_adapter=dynamodb_adapter) if dynamodb_adapter else CustomSkillBuilder()

sb.add_request_handler(LaunchRequestHandler())
sb.add_request_handler(GetNextDeparturesIntentHandler())
sb.add_request_handler(SetFavoriteStopIntentHandler())
sb.add_request_handler(SetFavoriteLineIntentHandler())
sb.add_request_handler(SetFavoriteDirectionIntentHandler())
sb.add_request_handler(GetFavoriteIntentHandler())
sb.add_request_handler(ClearFavoriteIntentHandler())
sb.add_request_handler(ChangeStopIntentHandler())
sb.add_request_handler(ListLinesIntentHandler())
sb.add_request_handler(HelpIntentHandler())
sb.add_request_handler(CancelOrStopIntentHandler())
sb.add_request_handler(FallbackIntentHandler())
sb.add_request_handler(SessionEndedRequestHandler())

sb.add_exception_handler(CatchAllExceptionHandler())

lambda_handler = sb.lambda_handler()
