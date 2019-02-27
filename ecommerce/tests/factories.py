import factory
from datetime import datetime
from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from factory.fuzzy import FuzzyText  # pylint: disable=ungrouped-imports
from faker import Faker
from oscar.core.loading import get_model
from oscar.test.factories import StockRecordFactory as OscarStockRecordFactory
from oscar.test.factories import ProductFactory

from ecommerce.core.models import SiteConfiguration


class PartnerFactory(factory.DjangoModelFactory):
    class Meta(object):
        model = get_model('partner', 'Partner')
        django_get_or_create = ('name',)

    name = FuzzyText(prefix='test-partner-')
    short_code = FuzzyText(length=8)


class SiteFactory(factory.DjangoModelFactory):
    class Meta(object):
        model = Site

    domain = FuzzyText(suffix='.fake')
    name = FuzzyText()


class SiteConfigurationFactory(factory.DjangoModelFactory):
    class Meta(object):
        model = SiteConfiguration

    lms_url_root = factory.LazyAttribute(lambda obj: "http://lms.testserver.fake")
    site = factory.SubFactory(SiteFactory)
    partner = factory.SubFactory(PartnerFactory)
    segment_key = 'fake_key'
    send_refund_notifications = False
    enable_sdn_check = False
    enable_embargo_check = False
    enable_partial_program = False
    discovery_api_url = 'http://{}.fake/'.format(Faker().domain_name())


class StockRecordFactory(OscarStockRecordFactory):
    product = factory.SubFactory(ProductFactory)
    price_currency = 'USD'


class UserFactory(factory.django.DjangoModelFactory):
    """
    User Factory.

    Creates an instance of User with minimal boilerplate
    """
    class Meta(object):
        model = User
        django_get_or_create = ('email', 'username')

    _DEFAULT_PASSWORD = 'test'

    username = factory.Sequence(u'robot{0}'.format)
    email = factory.Sequence(u'robot+test+{0}@edx.org'.format)
    password = factory.PostGenerationMethodCall('set_password', _DEFAULT_PASSWORD)
    first_name = factory.Sequence(u'Robot{0}'.format)
    last_name = 'Test'
    is_staff = factory.lazy_attribute(lambda x: False)
    is_active = True
    is_superuser = False
    last_login = datetime(2012, 1, 1)
    date_joined = datetime(2011, 1, 1)