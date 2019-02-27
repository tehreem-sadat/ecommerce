from mock import MagicMock, patch
from pytest import mark

from rest_framework.parsers import JSONParser
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory, force_authenticate

from ecommerce.extensions.api.permissions import CanActForUser, IsStaffOrEnterpriseUser
from ecommerce.tests.factories import UserFactory
from ecommerce.tests.testcases import TestCase


class PermissionsTestMixin(object):
    def get_request(self, user=None, data=None):
        request = APIRequestFactory().post('/', data)

        if user:
            force_authenticate(request, user=user)

        return Request(request, parsers=(JSONParser(),))


class CanActForUserTests(PermissionsTestMixin, TestCase):
    permissions_class = CanActForUser()

    def test_has_permission_no_data(self):
        """ If no username is supplied with the request data, return False. """
        request = self.get_request()
        self.assertFalse(self.permissions_class.has_permission(request, None))

    def test_has_permission_staff(self):
        """ Return True if request.user is a staff user. """
        user = self.create_user(is_staff=True)

        # Data is required, even if you're a staff user.
        request = self.get_request(user=user)
        self.assertFalse(self.permissions_class.has_permission(request, None))

        # Staff can create their own refunds
        request = self.get_request(user=user, data={'username': user.username})
        self.assertTrue(self.permissions_class.has_permission(request, None))

        # Staff can create refunds for other users
        request = self.get_request(user=user, data={'username': 'other_guy'})
        self.assertTrue(self.permissions_class.has_permission(request, None))

    def test_has_permission_same_user(self):
        """ If the request.data['username'] matches request.user, return True. """
        user = self.create_user()

        # Normal users can create their own refunds
        request = self.get_request(user=user, data={'username': user.username})
        self.assertTrue(self.permissions_class.has_permission(request, None))

        # Normal users CANNOT create refunds for other users
        request = self.get_request(user=user, data={'username': 'other_guy'})
        self.assertFalse(self.permissions_class.has_permission(request, None))


@mark.django_db
class TestIsStaffOrEnterpriseUser(TestCase):
    """
    Tests of the IsStaffOrEnterpriseUser permission
    """

    def setUp(self):
        super(TestIsStaffOrEnterpriseUser, self).setUp()
        self.enterprise_id = 'fake-enterprise-id'
        self.user = UserFactory()
        self.request = MagicMock(
            user=self.user,
            parser_context={
                'kwargs': {
                    'enterprise_id': self.enterprise_id
                }
            },
            session={},
            auth=MagicMock()
        )

        enterprise_api_client = patch('ecommerce.enterprise.permissions.EnterpriseApiClient')
        self.enterprise_api_client = enterprise_api_client.start()
        self.addCleanup(enterprise_api_client.stop)
        self.permission = IsStaffOrEnterpriseUser()

    def test_staff_access(self):
        self.user.is_staff = True
        self.assertTrue(self.permission.has_permission(self.request, None))

    def test_enterprise_learner_has_access(self):
        self.enterprise_api_client.return_value.get_enterprise_learner.return_value = {
            'enterprise_customer': {
                'uuid': self.enterprise_id
            },
            'groups': ['enterprise_data_api_access'],
        }
        self.assertTrue(self.permission.has_permission(self.request, None))

    def test_wrong_enterprise_learner_has_no_access(self):
        self.enterprise_api_client.return_value.get_enterprise_learner.return_value = {
            'enterprise_customer': {
                'uuid': 'some-other-enterprise-id'
            },
            'groups': ['enterprise_data_api_access'],
        }
        self.assertFalse(self.permission.has_permission(self.request, None))

    def test_enterprise_learner_has_no_group_access(self):
        self.enterprise_api_client.return_value.get_enterprise_learner.return_value = {
            'enterprise_customer': {
                'uuid': 'some-other-enterprise-id'
            },
            'groups': [],
        }
        self.assertFalse(self.permission.has_permission(self.request, None))

    def test_no_enterprise_learner_for_user(self):
        self.enterprise_api_client.return_value.get_enterprise_learner.return_value = {}
        self.assertFalse(self.permission.has_permission(self.request, None))
