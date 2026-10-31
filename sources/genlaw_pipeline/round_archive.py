"""Admit an exact member of a previously reviewed round input archive.

This binds the developer-authored PDF to the associated structured card/CAD.
It does not parse arbitrary production PDFs or certify their manufacturing data.
"""
import json
from pathlib import Path
from .cell_windows import checked_file,file_binding
from .round_surface_preparation import prepare


def checked(input_path,reviewed_archive):
    archive_path=checked_file(reviewed_archive);archive=json.loads(archive_path.read_text())
    if archive['schema']!='genlaw.round-surface-input-archive.v1' or archive['all_inputs_saved_before_NC'] is not True:
        raise ValueError('Require the reviewed pre-NC input archive')
    files=archive['files']
    if not 1<=len(files)<=512 or len({b['path'] for b in files})!=len(files):raise ValueError('Invalid archive membership')
    for binding in files:checked_file(binding)
    input_path=Path(input_path).resolve();binding=file_binding(input_path)
    if binding not in files:raise ValueError('Input is not an unchanged member of the reviewed archive')
    entry=json.loads(input_path.read_text());spec=entry['specification'];root=archive_path.parent
    specifications=root/'specifications.json'
    if file_binding(specifications) not in files:raise ValueError('Missing frozen specifications')
    candidates=[s for s in json.loads(specifications.read_text())['cases'] if s['id']==spec['id']]
    if len(candidates)!=1 or candidates[0]!=spec or input_path!=root/spec['id']/'input.json':
        raise ValueError('Input and archived specification disagree')
    for key,name in [('CAD','target.brep'),('CAD_reference','reference.json'),('process_plan','process-plan.json'),('paper_card','process-card.pdf')]:
        if entry[key] not in files or checked_file(entry[key])!=input_path.parent/name:
            raise ValueError('Foreign paper, CAD or structured-card association: '+key)
    plan=prepare(input_path)
    return plan,{'schema':'genlaw.reviewed-round-archive-admission.v1','status':'admitted',
        'reviewed_archive':reviewed_archive,'input':binding,'input_artifacts':plan['input_bindings'],
        'source_code':file_binding(Path(__file__)),'arbitrary_PDF_semantics_parsed':False,
        'input_kind':'developer-authored synthetic paper card with structured execution attachment',
        'manufacturing_approval':False}
