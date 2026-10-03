-- Fleet Management and Analytics System: schema (MySQL 8.0.16+, CHECKs enforced)
SET FOREIGN_KEY_CHECKS = 0;
DROP TABLE IF EXISTS maintenance_parts, maintenance_records, fuel_records, trips, vehicle_assignments, drivers, vehicles, users;
SET FOREIGN_KEY_CHECKS = 1;

CREATE TABLE users (
    user_id       INT AUTO_INCREMENT PRIMARY KEY,
    name          VARCHAR(100) NOT NULL,
    email         VARCHAR(150) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role          VARCHAR(20)  NOT NULL,
    CONSTRAINT uq_users_email UNIQUE (email),
    CONSTRAINT ck_users_role CHECK (role IN ('Administrator','Fleet Manager','Operator'))
) ENGINE=InnoDB;

CREATE TABLE vehicles (
    vehicle_id      INT AUTO_INCREMENT PRIMARY KEY,
    registration_no VARCHAR(20)   NOT NULL,
    type            VARCHAR(20)   NOT NULL,
    make            VARCHAR(50)   NOT NULL,
    model           VARCHAR(50)   NOT NULL,
    year            SMALLINT      NOT NULL,
    fuel_type       VARCHAR(10)   NOT NULL DEFAULT 'Diesel',
    odometer        DECIMAL(10,1) NOT NULL DEFAULT 0,
    status          VARCHAR(20)   NOT NULL DEFAULT 'Active',
    CONSTRAINT uq_vehicles_reg UNIQUE (registration_no),
    CONSTRAINT ck_vehicles_type CHECK (type IN ('Truck','Van','Pickup','Sedan')),
    CONSTRAINT ck_vehicles_fuel CHECK (fuel_type IN ('Diesel','Petrol')),
    CONSTRAINT ck_vehicles_odo CHECK (odometer >= 0),
    CONSTRAINT ck_vehicles_status CHECK (status IN ('Active','Under Maintenance','Inactive'))
) ENGINE=InnoDB;

CREATE TABLE drivers (
    driver_id      INT AUTO_INCREMENT PRIMARY KEY,
    name           VARCHAR(100) NOT NULL,
    phone          VARCHAR(20),
    license_no     VARCHAR(30)  NOT NULL,
    license_expiry DATE         NOT NULL,
    status         VARCHAR(10)  NOT NULL DEFAULT 'Active',
    CONSTRAINT uq_drivers_license UNIQUE (license_no),
    CONSTRAINT ck_drivers_status CHECK (status IN ('Active','Inactive'))
) ENGINE=InnoDB;

CREATE TABLE vehicle_assignments (
    assignment_id INT AUTO_INCREMENT PRIMARY KEY,
    vehicle_id    INT  NOT NULL,
    driver_id     INT  NOT NULL,
    start_date    DATE NOT NULL,
    end_date      DATE NULL,
    CONSTRAINT fk_va_vehicle FOREIGN KEY (vehicle_id) REFERENCES vehicles(vehicle_id) ON DELETE RESTRICT,
    CONSTRAINT fk_va_driver  FOREIGN KEY (driver_id)  REFERENCES drivers(driver_id)   ON DELETE RESTRICT,
    CONSTRAINT ck_va_dates CHECK (end_date IS NULL OR end_date >= start_date),
    INDEX idx_va_vehicle (vehicle_id),
    INDEX idx_va_driver (driver_id)
) ENGINE=InnoDB;

CREATE TABLE trips (
    trip_id     INT AUTO_INCREMENT PRIMARY KEY,
    vehicle_id  INT          NOT NULL,
    driver_id   INT          NOT NULL,
    origin      VARCHAR(100) NOT NULL,
    destination VARCHAR(100) NOT NULL,
    distance    DECIMAL(8,1) NOT NULL,
    start_date  DATE         NOT NULL,
    end_date    DATE         NOT NULL,
    status      VARCHAR(15)  NOT NULL DEFAULT 'Planned',
    CONSTRAINT fk_trips_vehicle FOREIGN KEY (vehicle_id) REFERENCES vehicles(vehicle_id) ON DELETE RESTRICT,
    CONSTRAINT fk_trips_driver  FOREIGN KEY (driver_id)  REFERENCES drivers(driver_id)   ON DELETE RESTRICT,
    CONSTRAINT ck_trips_distance CHECK (distance >= 0),
    CONSTRAINT ck_trips_dates CHECK (end_date >= start_date),
    CONSTRAINT ck_trips_status CHECK (status IN ('Planned','In Progress','Completed','Cancelled')),
    INDEX idx_trips_vehicle_dates (vehicle_id, start_date, end_date),
    INDEX idx_trips_driver (driver_id)
) ENGINE=InnoDB;

CREATE TABLE fuel_records (
    fuel_id         INT AUTO_INCREMENT PRIMARY KEY,
    vehicle_id      INT           NOT NULL,
    date            DATE          NOT NULL,
    odometer        DECIMAL(10,1) NOT NULL,
    quantity        DECIMAL(8,2)  NOT NULL,
    price_per_litre DECIMAL(6,2)  NOT NULL,
    total_cost      DECIMAL(10,2) NOT NULL,
    full_tank       BOOLEAN       NOT NULL DEFAULT TRUE,
    CONSTRAINT fk_fuel_vehicle FOREIGN KEY (vehicle_id) REFERENCES vehicles(vehicle_id) ON DELETE RESTRICT,
    CONSTRAINT ck_fuel_qty CHECK (quantity > 0),
    CONSTRAINT ck_fuel_price CHECK (price_per_litre > 0),
    CONSTRAINT ck_fuel_odo CHECK (odometer >= 0),
    INDEX idx_fuel_vehicle_date (vehicle_id, date)
) ENGINE=InnoDB;

CREATE TABLE maintenance_records (
    maintenance_id    INT AUTO_INCREMENT PRIMARY KEY,
    vehicle_id        INT           NOT NULL,
    service_type      VARCHAR(30)   NOT NULL,
    service_date      DATE          NOT NULL,
    odometer          DECIMAL(10,1) NOT NULL,
    cost              DECIMAL(10,2) NOT NULL DEFAULT 0,
    technician        VARCHAR(100),
    next_service_date DATE          NULL,
    status            VARCHAR(15)   NOT NULL DEFAULT 'Scheduled',
    CONSTRAINT fk_maint_vehicle FOREIGN KEY (vehicle_id) REFERENCES vehicles(vehicle_id) ON DELETE RESTRICT,
    CONSTRAINT ck_maint_cost CHECK (cost >= 0),
    CONSTRAINT ck_maint_odo CHECK (odometer >= 0),
    CONSTRAINT ck_maint_type CHECK (service_type IN
        ('Routine Service','Oil Change','Tyres','Brakes','Breakdown Repair','Inspection')),
    CONSTRAINT ck_maint_status CHECK (status IN ('Scheduled','In Progress','Completed')),
    INDEX idx_maint_vehicle_date (vehicle_id, service_date)
) ENGINE=InnoDB;

CREATE TABLE maintenance_parts (
    part_id        INT AUTO_INCREMENT PRIMARY KEY,
    maintenance_id INT           NOT NULL,
    part_name      VARCHAR(100)  NOT NULL,
    quantity       INT           NOT NULL,
    unit_cost      DECIMAL(10,2) NOT NULL,
    CONSTRAINT fk_parts_maint FOREIGN KEY (maintenance_id) REFERENCES maintenance_records(maintenance_id) ON DELETE RESTRICT,
    CONSTRAINT ck_parts_qty CHECK (quantity > 0),
    CONSTRAINT ck_parts_cost CHECK (unit_cost >= 0),
    INDEX idx_parts_maint (maintenance_id)
) ENGINE=InnoDB;
