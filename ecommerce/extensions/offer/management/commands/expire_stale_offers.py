"""
This command expires stale offers.
"""
from __future__ import unicode_literals

import logging

from datetime import datetime
from dateutil.relativedelta import relativedelta
from django.core.management import BaseCommand
from oscar.core.loading import get_model
from ecommerce.extensions.offer.constants import OFFER_ASSIGNMENT_EXPIRED, OFFER_ASSIGNMENT_REVOKED

OfferAssignment = get_model('offer', 'OfferAssignment')
logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Expires stale offers.

    Example:

        ./manage.py expire_stale_offers --period
    """

    help = 'Expire Stale Offers.'

    def add_arguments(self, parser):
        parser.add_argument('--period',
                            action='store',
                            dest='period',
                            type=int,
                            required=True,
                            help='Number of past years from the current date.')

    def handle(self, *args, **options):
        past_years = options['period']
        date_in_the_past = datetime.today() - relativedelta(years=past_years)

        offer_assignments = OfferAssignment.objects.filter(
            status__in=[OFFER_ASSIGNED, OFFER_ASSIGNMENT_EMAIL_PENDING],
            created__lte=date_in_the_past
        )
        for offer_assignment in offer_assignments:
            try:
                offer_assignment.status = OFFER_ASSIGNMENT_EXPIRED
                offer_assignment.save()
            except Exception as exc:  # pylint: disable=broad-except
                logger.exception('Encountered exception %s when expiring code %s for user %s',
                                 unicode(exc),
                                 offer_assignment.code,
                                 offer_assignment.user_email)
