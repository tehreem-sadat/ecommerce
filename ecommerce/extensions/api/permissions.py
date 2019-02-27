import logging
from rest_framework import permissions
from ecommerce.enterprise.clients import EnterpriseApiClient

LOGGER = logging.getLogger(__name__)


class CanActForUser(permissions.IsAdminUser):
    """
    Allows access only if the user has permission to perform operations for the user represented by the username field
    in request.data.
    """

    def has_permission(self, request, view):
        user = request.user
        username = request.data.get('username')

        if not username:
            return False

        return super(CanActForUser, self).has_permission(request, view) or (user and user.username == username)


class IsOffersOrIsAuthenticatedAndStaff(permissions.BasePermission):
    """ Permission that allows access to anonymous users to get course offers. """

    def has_permission(self, request, view):
        user = request.user
        return (user.is_authenticated() and user.is_staff) or view.action == 'offers'


class IsStaffOrOwner(permissions.BasePermission):
    """
    Permission that allows access to admin users or the owner of an object.
    The owner is considered the User object represented by obj.user.
    """

    def has_object_permission(self, request, view, obj):
        return request.user and (request.user.is_staff or obj.user == request.user)


class IsStaffOrModelPermissionsOrAnonReadOnly(permissions.DjangoModelPermissionsOrAnonReadOnly):
    """
    Permission that allows staff users and users that have been granted specific access to write,
    but allows read access to anyone.
    """
    def has_permission(self, request, view):
        user = request.user
        return user.is_staff or super(IsStaffOrModelPermissionsOrAnonReadOnly, self).has_permission(request, view)


class IsStaffOrEnterpriseUser(permissions.BasePermission):
    """
    Permission that checks to see if the request user is staff or is associated with the enterprise in the request.

    NOTE: This permission check may make a request to the LMS to get the enterprise association if it is not already in
    the session. This fetch should go away when JWT Scopes are fully implemented and the association is stored on
    the token.
    """

    ENTERPRISE_DATA_API_GROUP = 'enterprise_data_api_access'

    def get_user_enterprise_data(self, auth_token, user):
        """
        Get the enterprise learner model from the LMS for the given user.

        Returns: learner or None if unable to get or user is not associated with an enterprise
        """
        enterprise_client = EnterpriseApiClient(auth_token)
        enterprise_learner_data = enterprise_client.get_enterprise_learner(user)
        if not enterprise_learner_data:
            return None

        return {
            'enterprise_id': enterprise_learner_data['enterprise_customer']['uuid'],
            'enterprise_groups': enterprise_learner_data['groups'],
        }

    def has_permission(self, request, view):
        """
        Verify the user is staff or the associated enterprise matches the requested enterprise.
        """
        if request.user.is_staff:
            return True

        if not hasattr(request.session, 'enterprise_id') or not hasattr(request.session, 'enterprise_groups'):
            enterprise_data = self.get_user_enterprise_data(request.auth, request.user)
            if not enterprise_data:
                return False
            request.session['enterprise_id'] = enterprise_data['enterprise_id']
            request.session['enterprise_groups'] = enterprise_data['enterprise_groups']

        enterprise_in_url = request.parser_context.get('kwargs', {}).get('enterprise_id', '')

        permitted = (
            request.session['enterprise_id'] == enterprise_in_url and
            self.ENTERPRISE_DATA_API_GROUP in request.session['enterprise_groups']
        )
        if not permitted:
            LOGGER.warning('User {} denied access to EnterpriseEnrollments for enterprise {}'
                           .format(request.user, enterprise_in_url))

        return permitted
