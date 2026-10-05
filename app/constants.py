"""Status and role vocabularies (ARCHITECTURE.md 7.3). The CHECK constraints in sql/schema.sql mirror these."""
ADMIN, MANAGER, OPERATOR = "Administrator", "Fleet Manager", "Operator"
ROLES = (ADMIN, MANAGER, OPERATOR)

VEHICLE_TYPES = ("Truck", "Van", "Pickup", "Sedan")
FUEL_TYPES = ("Diesel", "Petrol")
VEHICLE_STATUSES = ("Active", "Under Maintenance", "Inactive")
ACTIVE_INACTIVE = ("Active", "Inactive")

OPEN_TRIP_STATUSES = ("Planned", "In Progress")
OPEN_SERVICE_STATUSES = ("Scheduled", "In Progress")

LICENCE_WARNING_DAYS = 30
