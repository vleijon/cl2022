# -*- mode: python; python-indent: 4 -*-
import ncs
from ncs.application import Service
from ncs.dp import Action
from ncs.maapi import single_read_trans, single_write_trans
from ncs.maagic import get_root, get_node

OPER = {
    1: 'MOP_CREATED',
    2: 'MOP_DELETED',
    3: 'MOP_MODIFIED',
    4: 'MOP_VALUE_SET',
    5: 'MOP_MOVED_AFTER',
    6: 'MOP_ATTR_SET'
}

# ---------------
# ACTIONS EXAMPLE
# ---------------
class ReportAction(Action):
    @Action.action
    def cb_action(self, uinfo, name, kp, input, output, trans):
        with single_read_trans('admin', 'system') as t:
            root = get_root(t)            
            result = ''
            for dev in root.devices.device:
                for iface in dev.live_status.interfaces_state.interface:
                    result += f'Device: {dev.name}, Interface: {iface.name}, Speed: {iface.speed}\n'
            self.log.info('Result: {}', result)
            output.result = result

class KickerAction(Action):
    def iterator(self, kp, op, oldv, newv):
         self.log.info(f'kp={kp}, op={OPER[op]}, newv={newv}')
         return ncs.ITER_RECURSE

    @Action.action
    def cb_action(self, uinfo, name, kp, input, output, trans):
        self.log.info(f'Triggering kicker {input.kicker_id}, ' + 
                      f'for path {input.path}, tid: {input.tid}')
        with ncs.maapi.Maapi() as m:
            trans = m.attach(input.tid)
            trans.diff_iterate(self.iterator, ncs.ITER_WANT_ATTR)
            m.detach(input.tid)

class ApproveAction(Action):
    @Action.action
    def cb_action(self, uinfo, name, kp, input, output, trans):
        self.log.info(f'Action approve {kp}')
        with single_write_trans('approve', 'system') as trans:
            p = get_node(trans, kp)
            p.approval.approved = True
            if input.comment:
                p.approval.comment = input.comment
            trans.apply()

class RequestApproval(Action):
    @Action.action
    def cb_action(self, uinfo, name, kp, input, output, trans):
        self.log.info(f'Action request approval {kp}')
        # Do a dry run and collect output
        msg = "Please approve: \n"
        with single_write_trans('admin', 'system') as trans:
            svc = get_node(trans, kp)
            svcname = svc.name
            # User override-approval to enable a dry-run
            svc.override_approval = True
            cp = ncs.maapi.CommitParams()
            cp.dry_run_native()
            rv = trans.apply_params(True, cp)
            if 'device' in rv:
                devices = rv['device']
                for dev in devices:
                    msg += f"Device {dev}:\n {devices[dev]}"
                self.log.info("Message: ", msg)
        # Create the approval record
        with single_write_trans('admin', 'system') as trans:
            root = get_root(trans)
            approval = root.approval.create(svcname)
            approval.text = msg
            trans.apply()

# ------------------------
# SERVICE CALLBACK EXAMPLE
# ------------------------
class ServiceCallbacks(Service):

    # The create() callback is invoked inside NCS FASTMAP and
    # must always exist.
    @Service.create
    def cb_create(self, tctx, root, service, proplist):
        self.log.info('Service create(service=', service._path, ')')

class NanoApprovedCallback(ncs.application.NanoService):
    @ncs.application.NanoService.create
    def cb_nano_create(self, tctx, root, service, plan, component, state,
                       proplist, component_proplist):
            self.log.info("Service nano-callback create post approval")
            vars = ncs.template.Variables()
            template = ncs.template.Template(service)
            template.apply('vpn', vars)

class NanoWaitingApprovalCallback(ncs.application.NanoService):
    @ncs.application.NanoService.create
    def cb_nano_create(self, tctx, root, service, plan, component, state,
                       proplist, component_proplist):
            self.log.info("Service nano-callback create WAITING approval")
            self.log.info(f"Component {component} {str(component)}")
            root.approval.create(service.name)

# ---------------------------------------------
# COMPONENT THREAD THAT WILL BE STARTED BY NCS.
# ---------------------------------------------
class Main(ncs.application.Application):
    def setup(self):
        # The application class sets up logging for us. It is accessible
        # through 'self.log' and is a ncs.log.Log instance.
        self.log.info('Main RUNNING')

        # Service callbacks require a registration for a 'service point',
        # as specified in the corresponding data model.
        #
        self.register_nano_service('vpn-servicepoint', 'ncs:self', 'vpn:approved',
            NanoApprovedCallback)
        self.register_nano_service('vpn-servicepoint', 'ncs:self', 'vpn:waiting-approval',
            NanoWaitingApprovalCallback)
        
        # When using actions, this is how we register them:
        #
        self.register_action('kick-action', KickerAction)
        self.register_action('vpn-action', ReportAction)
        self.register_action('approve', ApproveAction)
        self.register_action('requestapproval', RequestApproval)

        # If we registered any callback(s) above, the Application class
        # took care of creating a daemon (related to the service/action point).

        # When this setup method is finished, all registrations are
        # considered done and the application is 'started'.

    def teardown(self):
        # When the application is finished (which would happen if NCS went
        # down, packages were reloaded or some error occurred) this teardown
        # method will be called.

        self.log.info('Main FINISHED')
