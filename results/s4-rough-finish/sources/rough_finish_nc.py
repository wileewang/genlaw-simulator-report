"""Validate two native pocket operations without rewriting their NC."""
from .programming import nc_words
from .sequence_nc import HEADER, SHUTDOWN, segments
from .pocket_nc import check_nc as check_pocket
from .rough_finish_preparation import CAPABILITY, guard_plan


def label(index, operation):
    return f'RF_{index + 1:02d}_{operation["stage"].upper()}'


def check_nc(text, plan, completed=2):
    if plan.get('capability') != CAPABILITY or completed not in (1, 2):
        raise ValueError('Unsupported rough/finish NC contract')
    operations = plan['operations']
    if len(operations) != 2 or [op['stage'] for op in operations] != ['rough', 'finish']:
        raise ValueError('Need the original ordered rough/finish plan')
    # The initial adapter deliberately supports one unchanged tool controller.
    if operations[0]['parameters'] != operations[1]['parameters']:
        raise ValueError('Different rough/finish cutting presets require controller-transition support')
    items, blocks = segments(text, HEADER, SHUTDOWN)
    params = operations[0]['parameters']
    tool_words = f'M5\nM6 T1\nM3 S{params["spindle_rpm"]:g}\n'
    if len(items) != completed + 1 or items[0]['label'] != 'TC_RF_T1' or items[0]['blocks'] != nc_words(tool_words):
        raise ValueError('Unexpected tool/spindle transition or operation count')
    checks, mapping, trace = [], [], []
    elapsed = 0.
    for index, operation in enumerate(operations[:completed]):
        item = items[index + 1]
        if item['label'] != label(index, operation) or not item['blocks']:
            raise ValueError('NC rough/finish order differs from the process card')
        wrapper = HEADER + tool_words + '\n'.join(item['lines']) + '\n' + SHUTDOWN
        checked = check_pocket(wrapper, guard_plan(plan, operation))
        shift = item['first_executable_block'] - 6
        for point in checked['motion_trace']:
            point = {**point, 'block': point['block'] + shift,
                     'feed_time_end_seconds': point['feed_time_end_seconds'] + elapsed,
                     'step_id': operation['step_id'], 'stage': operation['stage']}
            if trace:
                point['start'] = [a if a is not None else b for a, b in zip(point['start'], trace[-1]['end'])]
                point['end'] = [a if a is not None else b for a, b in zip(point['end'], point['start'])]
            trace.append(point)
        elapsed += checked['feed_time_seconds']
        checks.append({k: v for k, v in checked.items() if k not in ('motion_trace', 'step_map')})
        mapping.append({'step_id': operation['step_id'], 'stage': operation['stage'],
                        'allowance_mm': operation['allowance_mm'],
                        'first_executable_block': item['first_executable_block'],
                        'last_executable_block': item['last_executable_block']})
    if sum(len(item['blocks']) for item in items) + len(nc_words(HEADER + SHUTDOWN)) != len(blocks):
        raise ValueError('Unaccounted executable blocks')
    return {'status': 'checked_rough_finish_native_nc', 'completed_operations': completed,
            'text_modified': False, 'card_order_checked': True, 'executable_blocks': len(blocks),
            'operation_checks': checks, 'step_map': mapping, 'motion_trace': trace,
            'feed_time_seconds': elapsed, 'rapid_time_not_included': True,
            'scope': 'Same-tool absolute XYZ reference subset; stage envelopes, depth, feeds and floor coverage.',
            'guard_wrapper_used_only_in_memory': True}


def check_prefix(prefix, full, plan):
    final = check_nc(full, plan)
    partial = check_nc(prefix, plan, completed=1)
    last = final['step_map'][0]['last_executable_block']
    if nc_words(prefix) != nc_words(full)[:last] + nc_words(SHUTDOWN):
        raise ValueError('Native rough export differs from the continuous program prefix')
    return {'status': 'identical_executable_prefix_then_standard_shutdown',
            'full_nc_last_included_executable_block': last,
            'prefix_executable_blocks': partial['executable_blocks'],
            'postprocessor_exported': True, 'nc_text_manually_spliced': False,
            'stock_reset_between_selected_operations': False}
