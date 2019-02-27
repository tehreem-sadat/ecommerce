"""
Helper methods for enterprise app.
"""
import hashlib
import hmac
import logging
import six
from collections import OrderedDict
from urllib import urlencode

import waffle
from django.conf import settings
from django.urls import reverse
from django.utils.translation import ugettext as _
from edx_django_utils.cache import TieredCache
from edx_rest_api_client.client import EdxRestApiClient
from oscar.core.loading import get_model
from requests.exceptions import ConnectionError, Timeout
from slumber.exceptions import SlumberHttpBaseException

from ecommerce.core.utils import deprecated_traverse_pagination
from ecommerce.enterprise.exceptions import EnterpriseDoesNotExist
from ecommerce.extensions.offer.models import OFFER_PRIORITY_ENTERPRISE

ConditionalOffer = get_model('offer', 'ConditionalOffer')
StockRecord = get_model('partner', 'StockRecord')
Voucher = get_model('voucher', 'Voucher')
CONSENT_FAILED_PARAM = 'consent_failed'
log = logging.getLogger(__name__)


def is_enterprise_feature_enabled():
    """
    Returns boolean indicating whether enterprise feature is enabled or
    disabled.

    Example:
        >> is_enterprise_feature_enabled()
        True

    Returns:
         (bool): True if enterprise feature is enabled else False

    """
    is_enterprise_enabled = waffle.switch_is_active(settings.ENABLE_ENTERPRISE_ON_RUNTIME_SWITCH)
    return is_enterprise_enabled


def get_enterprise_api_client(site):
    """
    Constructs a REST client for to communicate with the Open edX Enterprise Service
    """
    return EdxRestApiClient(
        site.siteconfiguration.enterprise_api_url,
        jwt=site.siteconfiguration.access_token
    )


def get_enterprise_customer(site, uuid):
    """
    Return a single enterprise customer
    """
    resource = 'enterprise-customer'
    cache_key = '{site_domain}_{partner_code}_{resource}_{enterprise_uuid}'.format(
        site_domain=site.domain,
        partner_code=site.siteconfiguration.partner.short_code,
        resource=resource,
        enterprise_uuid=uuid,
    )
    cache_key = hashlib.md5(cache_key).hexdigest()
    cached_response = TieredCache.get_cached_response(cache_key)
    if cached_response.is_found:
        return cached_response.value

    client = get_enterprise_api_client(site)
    path = [resource, str(uuid)]
    client = reduce(getattr, path, client)

    try:
        response = client.get()
    except (ConnectionError, SlumberHttpBaseException, Timeout):
        return None

    enterprise_customer_response = {
        'name': response['name'],
        'id': response['uuid'],
        'enable_data_sharing_consent': response['enable_data_sharing_consent'],
        'enforce_data_sharing_consent': response['enforce_data_sharing_consent'],
        'contact_email': response.get('contact_email', ''),
        'slug': response.get('slug')
    }

    TieredCache.set_all_tiers(
        cache_key,
        enterprise_customer_response,
        settings.ENTERPRISE_CUSTOMER_RESULTS_CACHE_TIMEOUT
    )

    return enterprise_customer_response


def get_enterprise_customers(site):
    resource = 'enterprise-customer'
    client = get_enterprise_api_client(site)
    endpoint = getattr(client, resource)
    response = endpoint.get()
    return sorted(
        [
            {
                'name': each['name'],
                'id': each['uuid'],
            }
            for each in deprecated_traverse_pagination(response, endpoint)
        ],
        key=lambda k: k['name'].lower()
    )


def get_enterprise_customer_consent_failed_context_data(request, voucher):
    """
    Get the template context to display a message informing the user that they were not enrolled in the course
    due to not consenting to data sharing with the Enterprise Customer.

    If the `consent_failed` GET param is defined and it's not set to a valid SKU, return an error context that
    says the given SKU doesn't exist.
    """
    consent_failed_sku = request.GET.get(CONSENT_FAILED_PARAM)
    if consent_failed_sku is None:
        # If no SKU was supplied via the consent failure param, then don't display any messages.
        return {}

    # The user is redirected to this view with `consent_failed` defined (as the product SKU) when the
    # user doesn't consent to data sharing.
    try:
        product = StockRecord.objects.get(partner_sku=consent_failed_sku).product
    except StockRecord.DoesNotExist:
        return {'error': _('SKU {sku} does not exist.').format(sku=consent_failed_sku)}

    # Return the view with an info message informing the user that the enrollment didn't complete.
    enterprise_customer = get_enterprise_customer_from_voucher(
        request.site,
        voucher
    )
    if not enterprise_customer:
        return {'error': _('There is no Enterprise Customer associated with SKU {sku}.').format(
            sku=consent_failed_sku
        )}

    contact_info = enterprise_customer['contact_email']

    # Use two full messages instead of using a computed string, so that translation utilities will pick up on both
    # strings as unique.
    message = _('If you have concerns about sharing your data, please contact your administrator at {enterprise}.')
    if contact_info:
        message = _(
            'If you have concerns about sharing your data, please contact your administrator at {enterprise} at '
            '{contact_info}.'
        )

    return {
        'info': {
            'title': _('Enrollment in {course_name} was not complete.').format(course_name=product.course.name),
            'message': message.format(enterprise=enterprise_customer['name'], contact_info=contact_info,)
        }
    }


def get_or_create_enterprise_customer_user(site, enterprise_customer_uuid, username):
    """
    Create a new EnterpriseCustomerUser on the enterprise service if one doesn't already exist.
    Return the EnterpriseCustomerUser data.
    """
    data = {
        'enterprise_customer': str(enterprise_customer_uuid),
        'username': username,
    }
    api_resource_name = 'enterprise-learner'
    api = site.siteconfiguration.enterprise_api_client
    endpoint = getattr(api, api_resource_name)

    get_response = endpoint.get(**data)
    if get_response.get('count') == 1:
        result = get_response['results'][0]
        return result

    response = endpoint.post(data)
    return response


def get_enterprise_course_enrollment(site, enterprise_customer_user, course_id):
    """
    Get the EnterpriseCourseEnrollment between a particular EnterpriseCustomerUser and
    course ID if it exists; if it doesn't exist, return None.

    Args:
        site (Site): The site which is handling the current request
        enterprise_customer_user (int): The primary key of the EnterpriseCustomerUser in the LMS
        course_id (str): The identifier of the course in the LMS

    Returns:
        dict: The single enterprise course enrollment linked to the username and course ID, if it exists
        NoneType: Return None if no matching enterprise course enrollment was found
    """
    api = site.siteconfiguration.enterprise_api_client
    api_resource = 'enterprise-course-enrollment'
    endpoint = getattr(api, api_resource)
    response = endpoint.get(
        enterprise_customer_user=int(enterprise_customer_user),
        course_id=str(course_id),
    )
    results = response.get('results')

    return results[0] if results else None


def enterprise_customer_user_needs_consent(site, enterprise_customer_uuid, course_id, username):
    """
    Determine if, for a particular username/EC UUID/course ID combination, the user must provide consent.

    Args:
        site (Site): The site which is handling the consent-sensitive request
        enterprise_customer_uuid (str): The UUID of the relevant EnterpriseCustomer
        course_id (str): The ID of the relevant course for enrollment
        username (str): The username of the user attempting to enroll into the course

    Returns:
        bool: Whether the user specified by the username argument must provide data
            sharing consent prior to being allowed to take advantage of the benefit
            that the EnterpriseCustomer specified by the enterprise_customer_uuid
            argument provides for the course specified by the course_id argument.
    """
    consent_client = site.siteconfiguration.consent_api_client
    endpoint = consent_client.data_sharing_consent
    return endpoint.get(
        username=username,
        enterprise_customer_uuid=enterprise_customer_uuid,
        course_id=course_id
    )['consent_required']


def get_enterprise_customer_uuid_from_voucher(voucher):
    """
    Given a Voucher, find the associated Enterprise Customer UUID, if it exists.
    """
    enterprise_customer_uuid = None
    for offer in voucher.offers.all():
        if offer.benefit.range and offer.benefit.range.enterprise_customer:
            enterprise_customer_uuid = offer.benefit.range.enterprise_customer
        elif offer.condition.enterprise_customer_uuid:
            enterprise_customer_uuid = offer.condition.enterprise_customer_uuid

    return enterprise_customer_uuid


def get_enterprise_customer_from_voucher(site, voucher):
    """
    Given a Voucher, find the associated Enterprise Customer and retrieve data about
    that customer from the Enterprise service. If there is no Enterprise Customer
    associated with the Voucher, `None` is returned.
    """
    enterprise_customer_uuid = get_enterprise_customer_uuid_from_voucher(voucher)

    if enterprise_customer_uuid:
        enterprise_customer = get_enterprise_customer(site, enterprise_customer_uuid)
        if enterprise_customer is None:
            raise EnterpriseDoesNotExist(
                'Enterprise customer with UUID {uuid} does not exist in the Enterprise service.'.format(
                    uuid=enterprise_customer_uuid
                )
            )

        return enterprise_customer

    return None


def get_enterprise_course_consent_url(
        site,
        code,
        sku,
        consent_token,
        course_id,
        enterprise_customer_uuid,
        failure_url=None
):
    """
    Construct the URL that should be used for redirecting the user to the Enterprise service for
    collecting consent. The URL contains a specially crafted "next" parameter that will result
    in the user being redirected back to the coupon redemption view with the verified consent token.
    """
    base_url = '{protocol}://{domain}'.format(
        protocol=settings.PROTOCOL,
        domain=site.domain,
    )
    callback_url = '{base}{resource}?{params}'.format(
        base=base_url,
        resource=reverse('coupons:redeem'),
        params=urlencode({
            'code': code,
            'sku': sku,
            'consent_token': consent_token,
        })
    )
    failure_url = failure_url or '{base}{resource}?{params}'.format(
        base=base_url,
        resource=reverse('coupons:offer'),
        params=urlencode(OrderedDict([
            ('code', code),
            (CONSENT_FAILED_PARAM, sku),
        ])),
    )
    request_params = {
        'course_id': course_id,
        'enterprise_customer_uuid': enterprise_customer_uuid,
        'defer_creation': True,
        'next': callback_url,
        'failure_url': failure_url,
    }
    redirect_url = '{base}?{params}'.format(
        base=site.siteconfiguration.enterprise_grant_data_sharing_url,
        params=urlencode(request_params)
    )
    return redirect_url


def get_enterprise_customer_data_sharing_consent_token(access_token, course_id, enterprise_customer_uuid):
    """
    Generate a sha256 hmac token unique to an end-user Access Token, Course, and
    Enterprise Customer combination.
    """
    consent_token_hmac = hmac.new(
        str(access_token),
        '{course_id}_{enterprise_customer_uuid}'.format(
            course_id=course_id,
            enterprise_customer_uuid=enterprise_customer_uuid,
        ),
        digestmod=hashlib.sha256,
    )
    return consent_token_hmac.hexdigest()


def get_enterprise_customer_uuid(coupon_code):
    """
    Get Enterprise Customer UUID associated with given coupon.

    Arguments:
        coupon_code (str): Code for the enterprise coupon.

    Returns:
        (UUID): UUID for the enterprise customer associated with the given coupon.
    """
    try:
        voucher = Voucher.objects.get(code=coupon_code)
    except Voucher.DoesNotExist:
        return None

    return get_enterprise_customer_uuid_from_voucher(voucher)


def set_enterprise_customer_cookie(site, response, enterprise_customer_uuid, max_age=None):
    """
    Set cookie for the enterprise customer with enterprise customer UUID.

   The cookie is set to the base domain so that it is accessible to all services on the same domain.

    Arguments:
        site (Site): Django site object.
        response (HttpResponse): Django HTTP response object.
        enterprise_customer_uuid (UUID): Enterprise customer UUID
        max_age (int): Maximum age of the cookie (seconds), defaults to None (lasts only as long as browser session).

    Returns:
         response (HttpResponse): Django HTTP response object.
    """
    if site.siteconfiguration.base_cookie_domain:
        response.set_cookie(
            settings.ENTERPRISE_CUSTOMER_COOKIE_NAME, enterprise_customer_uuid,
            domain=site.siteconfiguration.base_cookie_domain,
            max_age=max_age,
            secure=True,
        )
    else:
        log.warning(
            'Skipping cookie for enterprise customer "%s" as base_cookie_domain '
            'is not set in site configuration for site "%s".', enterprise_customer_uuid, site.domain
        )

    return response


def has_enterprise_offer(basket):
    """
    Return True if the basket has an Enterprise-related offer applied.

    Arguments:
        basket (Basket): The basket object.

    Returns:
        boolean: True if the basket has an Enterprise-related offer applied, false otherwise.
    """
    for offer in basket.offer_discounts:
        if offer['offer'].priority == OFFER_PRIORITY_ENTERPRISE:
            return True
    return False


def get_enterprise_catalog(site, enterprise_catalog, limit, page):
    """
    Get the EnterpriseCustomerCatalog for a given catalog uuid.

    Args:
        site (Site): The site which is handling the current request
        enterprise_catalog (str): The uuid of the Enterprise Catalog
        limit (int): The number of results to return per page.
        page (int): The page number to fetch.

    Returns:
        dict: The result set containing the content objects associated with the Enterprise Catalog.
        NoneType: Return None if no catalog with that uuid is found.
    """
    resource = 'enterprise_catalogs'
    partner_code = site.siteconfiguration.partner.short_code
    cache_key = '{site_domain}_{partner_code}_{resource}_{catalog}_{limit}_{page}'.format(
        site_domain=site.domain,
        partner_code=partner_code,
        resource=resource,
        catalog=enterprise_catalog,
        limit=limit,
        page=page
    )
    cache_key = hashlib.md5(cache_key).hexdigest()

    cached_response = TieredCache.get_cached_response(cache_key)
    if cached_response.is_found:
        return cached_response.value

    client = get_enterprise_api_client(site)
    path = [resource, str(enterprise_catalog)]
    client = reduce(getattr, path, client)

    response = client.get(
        limit=limit,
        page=page,
    )
    TieredCache.set_all_tiers(cache_key, response, settings.CATALOG_RESULTS_CACHE_TIMEOUT)

    return response


def get_cache_key(**kwargs):
    """
    Get MD5 encoded cache key for given arguments.

    Here is the format of key before MD5 encryption.
        key1:value1__key2:value2 ...

    Example:
        >>> get_cache_key(site_domain="example.com", resource="enterprise_customer_users")
        # Here is key format for above call
        # "site_domain:example.com__resource:enterprise_customer_users"
        a54349175618ff1659dee0978e3149ca

    Arguments:
        **kwargs: Keyword arguments that need to be present in cache key.

    Returns:
         An MD5 encoded key uniquely identified by the key word arguments.
    """
    key = '__'.join(['{}:{}'.format(item, value) for item, value in six.iteritems(kwargs)])

    return hashlib.md5(key.encode('utf-8')).hexdigest()
