-- Catalog the planner chooses from: away venues, hotels, restaurants (+ menus) and bus companies.
-- Additive only: does not touch teams, profiles, trips, bookings or announcements.
-- Data comes from data/build_dataset.ipynb (names/locations from OpenStreetMap where
-- source = 'osm'; prices, rooms, menus and bus companies are mock values).

create table public.venues (
  id        text primary key,
  name      text not null,
  address   text not null default '',
  lat       double precision not null,
  lng       double precision not null,
  capacity  int
);

create table public.hotels (
  id                   text primary key,
  venue_id             text references public.venues(id) on delete set null,
  name                 text not null,
  address              text not null default '',
  lat                  double precision not null,
  lng                  double precision not null,
  stars                int check (stars between 1 and 5),
  price_per_night      numeric(10,2) not null check (price_per_night >= 0),
  room_capacity        int not null default 2 check (room_capacity > 0),
  rooms_available      int not null check (rooms_available >= 0),
  amenities            text[] not null default '{}',
  cancellation_policy  text not null default '',
  source               text not null default 'mock' check (source in ('osm', 'mock')),
  osm_id               text
);

create table public.restaurants (
  id                        text primary key,
  venue_id                  text references public.venues(id) on delete set null,
  name                      text not null,
  address                   text not null default '',
  lat                       double precision not null,
  lng                       double precision not null,
  cuisine                   text not null default 'american',
  halal_certified           boolean not null default false,
  group_capacity            int not null default 30 check (group_capacity > 0),
  takes_group_reservations  boolean not null default true,
  price_per_person          numeric(10,2) not null check (price_per_person >= 0),
  source                    text not null default 'mock' check (source in ('osm', 'mock')),
  osm_id                    text
);

create table public.menu_items (
  id             text primary key,
  restaurant_id  text not null references public.restaurants(id) on delete cascade,
  name           text not null,
  course         text not null check (course in ('main', 'side', 'dessert', 'drink')),
  price          numeric(10,2) not null check (price >= 0),
  diet_tags      text[] not null default '{}'
);

create table public.bus_options (
  id                    text primary key,
  company               text not null,
  vehicle               text not null default 'Motorcoach',
  seats                 int not null check (seats > 0),
  rate_per_km           numeric(10,2) not null check (rate_per_km >= 0),
  min_charge            numeric(10,2) not null default 0 check (min_charge >= 0),
  driver_overnight_fee  numeric(10,2) not null default 0 check (driver_overnight_fee >= 0),
  wifi                  boolean not null default false,
  luggage_bay           boolean not null default true
);

create index hotels_venue_idx on public.hotels(venue_id);
create index restaurants_venue_idx on public.restaurants(venue_id);
create index menu_items_restaurant_idx on public.menu_items(restaurant_id);
create index menu_items_diet_idx on public.menu_items using gin (diet_tags);

alter table public.venues enable row level security;
alter table public.hotels enable row level security;
alter table public.restaurants enable row level security;
alter table public.menu_items enable row level security;
alter table public.bus_options enable row level security;

-- Public reference data: anyone may read; only the Flask server (secret key) writes.
create policy "catalog readable" on public.venues for select to anon, authenticated using (true);
create policy "catalog readable" on public.hotels for select to anon, authenticated using (true);
create policy "catalog readable" on public.restaurants for select to anon, authenticated using (true);
create policy "catalog readable" on public.menu_items for select to anon, authenticated using (true);
create policy "catalog readable" on public.bus_options for select to anon, authenticated using (true);
