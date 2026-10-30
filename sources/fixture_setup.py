"""Versioned static fixture resources on the existing absolute-gauge XYZ setup."""
import copy
import re

SCHEMA = 'genlaw.setup.xyz-tool-length-fixtures.v1'
MODE = 'declared_static_tool_envelopes_vs_fixture_boxes'
FILE_KEY = 'fixture_geometry'
EXCLUSIONS = ['unmodeled_fixture_geometry', 'holder_vs_evolving_workpiece',
              'spindle_and_machine_body_collision', 'toolchanger_exchange_motion',
              'clamping_force', 'material_physics', 'surface_roughness']


def enabled(plan):
    setup = plan.get('setup')
    return isinstance(setup, dict) and setup.get('schema') == SCHEMA


def base_setup(setup):
    from .tool_length_setup import SCHEMA as TLO
    base = copy.deepcopy(setup)
    base.pop(FILE_KEY)
    base.update(schema=TLO, fixture_mode='unmodelled_explicitly_excluded')
    return base


def validate_setup(setup, stock_bounds):
    from .tool_length_setup import fields, validate_setup as validate_lengths
    if not isinstance(setup, dict) or set(setup) != fields() | {FILE_KEY} or setup['schema'] != SCHEMA or setup['fixture_mode'] != MODE:
        raise ValueError('Explicit fixture setup version and geometry reference required')
    ref = setup[FILE_KEY]
    if (not isinstance(ref, dict) or set(ref) != {'schema', 'file_key', 'sha256', 'source'}
            or ref['schema'] != 'genlaw.fixture-geometry-ref.v1' or ref['file_key'] != FILE_KEY
            or not isinstance(ref['sha256'], str) or re.fullmatch('[0-9a-f]{64}', ref['sha256']) is None
            or not isinstance(ref['source'], dict)):
        raise ValueError('Fixture geometry must be explicitly versioned, hashed and traced to an input document')
    # This projection validates only the unchanged XYZ/gauge mathematics; the
    # original setup, card and fixture declaration remain in the execution plan.
    return validate_lengths(base_setup(setup), stock_bounds)


def setup_requirements(setup):
    from .tool_length_setup import setup_requirements as base_requirements
    result = base_requirements(base_setup(setup))
    result.update(fixture_mode=MODE)
    result[FILE_KEY] = copy.deepcopy(setup[FILE_KEY])
    return result
