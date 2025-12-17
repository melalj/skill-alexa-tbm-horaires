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
        return handler_input.attributes_manager.persistent_attributes
    except Exception:
        return handler_input.attributes_manager.session_attributes or {}


def save_persistent_attributes(handler_input: HandlerInput, attrs: dict):
    """Save user preferences."""
    try:
        handler_input.attributes_manager.persistent_attributes = attrs
        handler_input.attributes_manager.save_persistent_attributes()
    except Exception:
        handler_input.attributes_manager.session_attributes = attrs


class LaunchRequestHandler(AbstractRequestHandler):
    """Handler for Skill Launch."""

    def can_handle(self, handler_input: HandlerInput) -> bool:
        return is_request_type("LaunchRequest")(handler_input)

    def handle(self, handler_input: HandlerInput) -> Response:
        attrs = get_persistent_attributes(handler_input)
        
        if attrs.get("stop_point_ref"):
            # User has a favorite stop configured
            stop_name = attrs.get("stop_name", "votre arrêt")
            line_name = attrs.get("line_name", "")
            speech = f"Bienvenue sur TBM Horaires. Votre arrêt favori est {stop_name}"
            if line_name:
                speech += f" pour la ligne {line_name}"
            speech += ". Dites 'prochain passage' pour les horaires, ou 'configurer' pour changer d'arrêt."
        else:
            speech = (
                "Bienvenue sur TBM Horaires ! "
                "Je peux vous donner les prochains passages des trams et bus de Bordeaux. "
                "Commencez par configurer votre arrêt en disant par exemple : "
                "'enregistre l'arrêt Gambetta pour le tram B direction Pessac'."
            )

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
        attrs = get_persistent_attributes(handler_input)
        
        # Check for slot values (user might specify stop/line in query)
        slot_stop = get_slot_value(handler_input, "stopName")
        slot_line = get_slot_value(handler_input, "lineName")
        
        # Use saved preferences if no slots provided
        stop_point_ref = attrs.get("stop_point_ref")
        line_ref = attrs.get("line_ref")
        direction_ref = attrs.get("direction_ref", -1)
        stop_name = attrs.get("stop_name")
        line_name = attrs.get("line_name")
        dest_name = attrs.get("dest_name")

        # If user specified a stop name in the query, try to find it
        if slot_stop or slot_line:
            # Search for the stop
            search_results = tbm_client.search_stop(
                stop_query=slot_stop,
                line_query=slot_line
            )
            if search_results:
                result = search_results[0]
                stop_point_ref = result.get("stop_point_ref")
                line_ref = result.get("line_ref")
                direction_ref = result.get("direction_ref", -1)
                stop_name = result.get("stop_name")
                line_name = result.get("line_name")
                dest_name = result.get("dest_name")
            else:
                search_term = slot_stop or slot_line
                speech = f"Je n'ai pas trouvé d'arrêt correspondant à {search_term}. Essayez avec un autre nom."
                return (
                    handler_input.response_builder
                    .speak(speech)
                    .ask("Quel arrêt cherchez-vous ?")
                    .response
                )

        if not stop_point_ref:
            speech = (
                "Vous n'avez pas encore configuré d'arrêt favori. "
                "Dites par exemple : 'enregistre l'arrêt Gambetta pour le tram B'."
            )
            return (
                handler_input.response_builder
                .speak(speech)
                .ask("Quel arrêt souhaitez-vous enregistrer ?")
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
            speech = f"Il n'y a pas de passage prévu pour {line_name or 'cette ligne'} à {stop_name} dans les prochaines minutes."
            return handler_input.response_builder.speak(speech).response

        # Build response
        line_label = line_name or "Le prochain"
        dest_label = f" direction {dest_name}" if dest_name else ""
        
        if len(departures) == 1:
            mins = _mins_to(departures[0].get("expected") or departures[0].get("aimed"))
            if mins == 0:
                speech = f"{line_label}{dest_label} arrive maintenant à {stop_name}."
            elif mins == 1:
                speech = f"{line_label}{dest_label} arrive dans 1 minute à {stop_name}."
            else:
                speech = f"{line_label}{dest_label} arrive dans {mins} minutes à {stop_name}."
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
            speech = f"{line_label}{dest_label} à {stop_name} : dans {times_str}."

        return handler_input.response_builder.speak(speech).response


class SetFavoriteStopIntentHandler(AbstractRequestHandler):
    """Handler for setting favorite stop."""

    def can_handle(self, handler_input: HandlerInput) -> bool:
        return is_intent_name("SetFavoriteStopIntent")(handler_input)

    def handle(self, handler_input: HandlerInput) -> Response:
        slot_stop = get_slot_value(handler_input, "stopName")
        slot_line = get_slot_value(handler_input, "lineName")
        slot_dest = get_slot_value(handler_input, "destinationName")

        if not slot_stop:
            speech = "Je n'ai pas compris le nom de l'arrêt. Pouvez-vous répéter ?"
            return (
                handler_input.response_builder
                .speak(speech)
                .ask("Quel est le nom de l'arrêt ?")
                .response
            )

        # Search for matching stops
        search_results = tbm_client.search_stop(
            stop_query=slot_stop,
            line_query=slot_line,
            dest_query=slot_dest
        )

        if not search_results:
            speech = f"Je n'ai pas trouvé d'arrêt '{slot_stop}'"
            if slot_line:
                speech += f" pour la ligne {slot_line}"
            speech += ". Essayez avec un autre nom."
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
            "stop_name": result.get("stop_name"),
            "line_ref": result.get("line_ref"),
            "line_name": result.get("line_name"),
            "direction_ref": result.get("direction_ref"),
            "dest_name": result.get("dest_name"),
        }
        save_persistent_attributes(handler_input, attrs)

        stop_name = result.get("stop_name")
        line_name = result.get("line_name", "")
        dest_name = result.get("dest_name", "")

        speech = f"C'est noté ! J'ai enregistré l'arrêt {stop_name}"
        if line_name:
            speech += f" pour la ligne {line_name}"
        if dest_name:
            speech += f" direction {dest_name}"
        speech += ". Dites 'prochain passage' pour les horaires."

        return (
            handler_input.response_builder
            .speak(speech)
            .ask("Voulez-vous connaître les prochains passages ?")
            .response
        )


class GetFavoriteIntentHandler(AbstractRequestHandler):
    """Handler for getting current favorite stop."""

    def can_handle(self, handler_input: HandlerInput) -> bool:
        return is_intent_name("GetFavoriteIntent")(handler_input)

    def handle(self, handler_input: HandlerInput) -> Response:
        attrs = get_persistent_attributes(handler_input)
        
        if not attrs.get("stop_point_ref"):
            speech = "Vous n'avez pas encore d'arrêt favori configuré."
            return handler_input.response_builder.speak(speech).ask("Voulez-vous en configurer un ?").response

        stop_name = attrs.get("stop_name", "inconnu")
        line_name = attrs.get("line_name", "")
        dest_name = attrs.get("dest_name", "")

        speech = f"Votre arrêt favori est {stop_name}"
        if line_name:
            speech += f" pour la ligne {line_name}"
        if dest_name:
            speech += f" direction {dest_name}"
        speech += "."

        return handler_input.response_builder.speak(speech).response


class ClearFavoriteIntentHandler(AbstractRequestHandler):
    """Handler for clearing favorite stop."""

    def can_handle(self, handler_input: HandlerInput) -> bool:
        return is_intent_name("ClearFavoriteIntent")(handler_input)

    def handle(self, handler_input: HandlerInput) -> Response:
        save_persistent_attributes(handler_input, {})
        speech = "J'ai supprimé votre arrêt favori. Vous pouvez en configurer un nouveau."
        return handler_input.response_builder.speak(speech).ask("Quel arrêt souhaitez-vous enregistrer ?").response


class ListLinesIntentHandler(AbstractRequestHandler):
    """Handler for listing available lines."""

    def can_handle(self, handler_input: HandlerInput) -> bool:
        return is_intent_name("ListLinesIntent")(handler_input)

    def handle(self, handler_input: HandlerInput) -> Response:
        speech = (
            "Les principales lignes TBM sont : "
            "les trams A, B, C et D, "
            "et les Lianes 1 à 16 pour les bus. "
            "Il y a aussi le Batcub pour les navettes fluviales. "
            "Pour configurer votre arrêt, dites par exemple : "
            "'enregistre l'arrêt Quinconces pour le tram C'."
        )
        return handler_input.response_builder.speak(speech).ask("Quelle ligne vous intéresse ?").response


class HelpIntentHandler(AbstractRequestHandler):
    """Handler for Help Intent."""

    def can_handle(self, handler_input: HandlerInput) -> bool:
        return is_intent_name("AMAZON.HelpIntent")(handler_input)

    def handle(self, handler_input: HandlerInput) -> Response:
        speech = (
            "Je peux vous donner les horaires des trams et bus TBM de Bordeaux en temps réel. "
            "Voici ce que vous pouvez faire : "
            "Dites 'enregistre l'arrêt' suivi du nom de l'arrêt et de la ligne pour sauvegarder votre arrêt favori. "
            "Ensuite, dites simplement 'prochain passage' pour connaître les horaires. "
            "Vous pouvez aussi dire 'quel est mon arrêt' pour voir votre configuration."
        )
        return handler_input.response_builder.speak(speech).ask("Que souhaitez-vous faire ?").response


class CancelOrStopIntentHandler(AbstractRequestHandler):
    """Handler for Cancel and Stop Intents."""

    def can_handle(self, handler_input: HandlerInput) -> bool:
        return (
            is_intent_name("AMAZON.CancelIntent")(handler_input)
            or is_intent_name("AMAZON.StopIntent")(handler_input)
        )

    def handle(self, handler_input: HandlerInput) -> Response:
        speech = "À bientôt sur TBM Horaires !"
        return handler_input.response_builder.speak(speech).response


class FallbackIntentHandler(AbstractRequestHandler):
    """Handler for Fallback Intent."""

    def can_handle(self, handler_input: HandlerInput) -> bool:
        return is_intent_name("AMAZON.FallbackIntent")(handler_input)

    def handle(self, handler_input: HandlerInput) -> Response:
        speech = (
            "Désolé, je n'ai pas compris. "
            "Vous pouvez dire 'prochain passage' pour les horaires, "
            "ou 'aide' pour plus d'informations."
        )
        return handler_input.response_builder.speak(speech).ask("Que souhaitez-vous faire ?").response


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
        speech = "Désolé, une erreur s'est produite. Veuillez réessayer."
        return handler_input.response_builder.speak(speech).ask("Que souhaitez-vous faire ?").response


# Skill Builder
sb = CustomSkillBuilder(persistence_adapter=dynamodb_adapter) if dynamodb_adapter else CustomSkillBuilder()

sb.add_request_handler(LaunchRequestHandler())
sb.add_request_handler(GetNextDeparturesIntentHandler())
sb.add_request_handler(SetFavoriteStopIntentHandler())
sb.add_request_handler(GetFavoriteIntentHandler())
sb.add_request_handler(ClearFavoriteIntentHandler())
sb.add_request_handler(ListLinesIntentHandler())
sb.add_request_handler(HelpIntentHandler())
sb.add_request_handler(CancelOrStopIntentHandler())
sb.add_request_handler(FallbackIntentHandler())
sb.add_request_handler(SessionEndedRequestHandler())

sb.add_exception_handler(CatchAllExceptionHandler())

lambda_handler = sb.lambda_handler()

