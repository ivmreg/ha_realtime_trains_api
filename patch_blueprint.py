import re

with open('blueprint.yaml', 'r') as f:
    content = f.read()

# 1. Update description
old_desc = """    Requires ONE rest_command in configuration.yaml (rtt_service is optional):
    1. rtt_search: https://api.rtt.io/api/v1/json/search/{{ origin }}/to/{{ destination }}/{{ date }}/{{ time }}"""
new_desc = """    Requires rest_commands in configuration.yaml (rtt_service is optional):
    rtt_search:
      url: "https://data.rtt.io/gb-nr/location?code={{ origin }}&filterTo={{ destination }}&timeFrom={{ date }}T{{ time[:2] }}:{{ time[2:] }}:00"
      headers:
        Authorization: "Bearer !secret rtt_token"
    rtt_service:
      url: "https://data.rtt.io/gb-nr/service?identity={{ uid }}&departureDate={{ date }}"
      headers:
        Authorization: "Bearer !secret rtt_token" """
content = content.replace(old_desc, new_desc)

# 2. Update today
content = content.replace("today: \"{{ now().strftime('%Y/%m/%d') }}\"", "today: \"{{ now().strftime('%Y-%m-%d') }}\"")

# 3. Update action block entirely
import textwrap

old_action_start = content.find("action:\n  # 1. Search")
if old_action_start != -1:
    new_action = """action:
  # 1. Search for the first scheduled time
  - service: rest_command.rtt_search
    data:
      origin: "{{ search_origin }}"
      destination: "{{ search_dest }}"
      date: "{{ today }}"
      time: "{{ search_times[0] }}"
    response_variable: search_response

  - variables:
      search_data: "{{ search_response.content | from_json if search_response.content is string else search_response.content }}"
      
      # Find first matching service from the user's list
      found_service_json: >
        {% set ns = namespace(res='null') %}
        {% if search_data.services is defined %}
          {% for t in search_times %}
            {% for s in search_data.services %}
              {% set s_time = s.temporalData.departure.scheduleAdvertised | default(s.temporalData.departure.scheduleInternal | default('')) %}
              {% if s_time != '' and s_time[11:13] ~ s_time[14:16] == t %}
                {% set ns.res = s | to_json %}
                {% break %}
              {% endif %}
            {% endfor %}
            {% if ns.res != 'null' %}{% break %}{% endif %}
          {% endfor %}
        {% endif %}
        {{ ns.res }}

      train_data: "{{ found_service_json | from_json if found_service_json != 'null' else none }}"

  # 2. If no train found, notify and stop
  - if:
      - condition: template
        value_template: "{{ train_data is none }}"
    then:
      - domain: mobile_app
        type: notify
        device_id: !input notify_device
        message: >
          {% if search_data is none %}
            ❌ API Error: Empty response from RTT
          {% elif search_data.systemStatus is defined and search_data.systemStatus.rttCore != 'OK' %}
            ❌ API Error: RTT Core Status {{ search_data.systemStatus.rttCore }}
          {% else %}
            ❓ Could not find trains from {{ search_origin }} to {{ search_dest }} at {{ search_times | join(', ') }}.
          {% endif %}
      - stop: "No matching service found"

  # 3. Extract details
  - variables:
      sched_meta: "{{ train_data.scheduleMetadata | default({}) }}"
      temp_data: "{{ train_data.temporalData | default({}) }}"
      loc_meta: "{{ train_data.locationMetadata | default({}) }}"
      
      uid: "{{ sched_meta.identity | default('') }}"
      id: "{{ sched_meta.trainReportingIdentity | default('N/A') }}"
      run_date: "{{ sched_meta.departureDate | default('') }}"
      
      # Status
      is_cancelled: "{{ temp_data.displayAs == 'CANCELLED' or (temp_data.departure is defined and temp_data.departure.isCancelled == true) }}"
      
      platform_actual: "{{ loc_meta.platform.actual if loc_meta.platform is defined else '' }}"
      platform_planned: "{{ loc_meta.platform.planned if loc_meta.platform is defined else '' }}"
      platform: "{{ platform_actual if platform_actual != '' else (platform_planned if platform_planned != '' else '—') }}"
      platform_changed: "{{ platform_actual != '' and platform_planned != '' and platform_actual != platform_planned }}"
      
      cancel_reason: >
        {% set reason = (train_data.reasons | default([])) | selectattr('type', 'eq', 'CANCEL') | first | default(none) %}
        {{ reason.shortText if reason else 'Unknown' }}
      
      # Times & Delay
      sched: "{{ temp_data.departure.scheduleAdvertised | default(temp_data.departure.scheduleInternal | default('')) }}"
      actual: "{{ temp_data.departure.realtimeActual | default(temp_data.departure.realtimeEstimate | default('')) }}"
      
      sched_display: "{{ (sched[11:16] if sched | length >= 16 else sched) | default('Unknown', true) }}"
      actual_display: "{{ (actual[11:16] if actual | length >= 16 else actual) | default('Unknown', true) }}"
      
      delay_min: >
        {% if sched and actual %}
          {% set s = as_timestamp(sched) %}
          {% set a = as_timestamp(actual) %}
          {% if s and a %}
            {% set d = (a - s) / 60 %}
            {{ d | int }}
          {% else %}0{% endif %}
        {% else %}0{% endif %}

  # 4. Optional detailed info
  - if:
      - condition: template
        value_template: "{{ fetch_detailed_info }}"
    then:
      - service: rest_command.rtt_service
        data:
          uid: "{{ uid }}"
          date: "{{ run_date if run_date else today }}"
        response_variable: rtt_response

  # 5. Notify
  - choose:
      - conditions: "{{ is_cancelled }}"
        sequence:
          - domain: mobile_app
            type: notify
            device_id: !input notify_device
            message: >
              ❌ Train {{ sched_display }} ({{ id }}) is CANCELLED
              Reason: {{ cancel_reason }}

      - conditions: "{{ delay_min | int > 0 }}"
        sequence:
          - domain: mobile_app
            type: notify
            device_id: !input notify_device
            message: >
              ⚠️ Train {{ sched_display }} ({{ id }}) is DELAYED
              Expected: {{ actual_display }}
              Delay: {{ delay_min }} min
              Platform: {{ platform }}{% if platform_changed %} (⚠️ CHANGED){% endif %}

      - conditions: "{{ platform_changed and delay_min | int <= 0 and not is_cancelled }}"
        sequence:
          - domain: mobile_app
            type: notify
            device_id: !input notify_device
            message: >
              ⚠️ Train {{ sched_display }} ({{ id }}) - PLATFORM CHANGED
              New Platform: {{ platform }}
              Departure: {{ actual_display }}

    default:
      - if:
          - condition: template
            value_template: "{{ not notify_only_on_issues }}"
        then:
          - domain: mobile_app
            type: notify
            device_id: !input notify_device
            message: >
              ✅ Train {{ sched_display }} ({{ id }}) ON TIME
              Platform: {{ platform }}

mode: single
"""
    content = content[:old_action_start] + new_action

with open('blueprint.yaml', 'w') as f:
    f.write(content)
