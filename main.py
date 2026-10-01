headings = [
    "car_id",
    "timestamp",
    "event_id",
    "event_name",
    "departure_station_id",
    "departure_station_name",
    "event_station_id",
    "event_station_name",
    "dest_station_id",
    "dest_station_name",
    "car_type_id",
    "car_type_name",
    "cargo_type_id",
    "cargo_type_name",
    "weight_t",
    "container_code",
    "o_sender",
    "sender",
    "o_recipient",
    "recipient",
    "profile"
]

from faker import Faker

faker = Faker()

towns = [faker.city() for i in range(10)]
print(towns)

