

import boto3
import os

elbname = os.environ['elbname']
inputsubnets = list(map(str.strip, os.environ['inputsubnets'].split(',')))
Routetargets = list(map(str.strip, os.environ['routetargets'].split(',')))


def lambda_handler(event, context):
    RouteSwitchv2(elbname, inputsubnets, Routetargets)
    return 'Hello from Lambda'


def RouteSwitchv2(elbname, inputsubnets, Routetargets):
    """ this is the main function. it sees if there are any InService GWs
    availabile and if there are it goes route table by route table (which,
    one level below translates to subnet by subnet, to the extent that they
    don't already share an RT) and sees if the GW associated with it can be
    optimized, and if it does, it switches the GWs in the RT
    """
    gwtable = get_GWs_by_LB(elbname)
    if sum([1 for g in gwtable if g[1] == 'InService']) > 0:
        DisableSourceDestCheck(gwtable)
        grsaz = createGRSAZ(gwtable, inputsubnets, Routetargets)
        for rt in set([r[1] for r in grsaz]):
            gwi = OptimalGWforRT(rt, gwtable, grsaz)
            if gwi != 'Current':
                for g in set([g[0] for g in grsaz if g[1] == rt]):
                    ReplaceGWforRTinAWS(g, gwi, grsaz, rt, gwtable, Routetargets)
                    grsaz = ReplaceGWsforRTinGRSAZ(g, gwi, grsaz, rt)
            else:
                print('route table ', rt, ' is already optimal in using the gateway(s): ', set([r[0] for r in grsaz if r[1] == rt]))    
    else:
        print("No GWs are up. Quiting")
    return 'finished executing'


def get_GWs_by_LB(elbname):
    """retrieves fron AWS all the GWs that are associated with an elb and arranges in a GW, GW health, AZ, VPC,eth0 and eth1 table, and the sunbnet of the instance (of eth0 when there's more than one IF)
    """
    M = []
    elb = boto3.client('elb')
    ec2 = boto3.client("ec2")
    ElbGW = elb.describe_instance_health(LoadBalancerName=elbname)
    GWs = ec2.describe_instances(Filters=[{"Name": "instance-id", "Values": [x['InstanceId'] for x in ElbGW['InstanceStates']]}])
    for re in GWs['Reservations']:
        for ins in re['Instances']:
            M.append([ins['InstanceId'],
                      [y['State'] for y in ElbGW['InstanceStates'] if y['InstanceId'] == ins['InstanceId']].pop(),
                      ins['Placement']['AvailabilityZone'],
                      ins['VpcId'],
                      sorted([
                              [eni['Attachment']['DeviceIndex'],
                               eni['NetworkInterfaceId'],
                               eni['SourceDestCheck']] for eni in ins['NetworkInterfaces']
                              ]
                             ),
                      ins['SubnetId']])
    return M


def DisableSourceDestCheck(gwtable):
    """ takes a gwtable and makes sure all the ENIs have source dest checks disabled
    """
    ec2 = boto3.client("ec2")
    for gw in gwtable:
        for networkif in gw[4]:
            if networkif[2] == True:
                ec2.modify_network_interface_attribute(NetworkInterfaceId=networkif[1],
                                                       SourceDestCheck={'Value': False})
                print('SourceDestCheck on ENI ', networkif[1], ' was set to false')


def createGRSAZ(gwtable, inputsubnets, Routetargets):
    """ this creates a table, a list of lists, with a row for each subnet. Each row has a 
    GW name (or NoGW), a route table name, a subnet name, the subnet AZ and a numeric indicator whether the Route table has all the routes and is 
    pointing to InService  GWs (1), or else has all the routes (2), or else it has no routes (3). In case 2 the GW name will be NoGW, 
    because the GWs that are set against the routes are not InService. In case 3 the function itself will have created a new Route table and assign
    it to the subnet before adding it to the table
    
    """
    ec2 = boto3.client("ec2")
    elb = boto3.client('elb')

    #clean the inputsubnets
    vpcid = elb.describe_load_balancers(LoadBalancerNames=[elbname])['LoadBalancerDescriptions'][0]['VPCId']
    subnetsvpc = ec2.describe_subnets(Filters=[{'Name': "vpc-id", 'Values': [vpcid]}])
    notrealsubnets = set(inputsubnets)-set([s['SubnetId'] for s in subnetsvpc['Subnets']])
    if len(notrealsubnets) > 0:
        print('the following are not real subnets in your VPC: ', notrealsubnets)
    cleaninputsubnets = list(set(inputsubnets) - notrealsubnets)

    #find all the routing tables already associated with any healthy gws and their associated subnets  
    rt2 = ec2.describe_route_tables(Filters=[{'Name': 'association.subnet-id', 'Values': cleaninputsubnets}])
    #disassociate subnets from RTs if used by gateway ...later

    M = []
    for r in rt2['RouteTables']:
        if set(Routetargets) <= set([rr['DestinationCidrBlock'] for rr in r['Routes'] if 'InstanceId' in rr.keys() and rr['InstanceId'] in [g[0] for g in gwtable if g[1] == 'InService']]):
            for s in [ass for ass in r['Associations'] if ass['SubnetId'] in cleaninputsubnets]:
                goodinstance = [rr['InstanceId'] for rr in r['Routes'] if 'InstanceId' in rr.keys() and rr['InstanceId'] in [g[0] for g in gwtable if g[1] == 'InService']].pop()
                M.append(tuple([goodinstance,
                                r['RouteTableId'],
                                s['SubnetId'],
                                1]))

        # add route tables that have the routes but no live GWs with index 2....we'll reuse these RTs and routes
        elif set(Routetargets) <= set([rr['DestinationCidrBlock'] for rr in r['Routes']]):
            for s in r['Associations']:
                M.append(tuple(['NoGW',
                                r['RouteTableId'],
                                s['SubnetId'],
                                2]))

    #add new RTs for any subnets that are not in the table. mark the GWs as NoGW and index at 3 so that we know that we need to add new routes
    subnets1 = ec2.describe_subnets(Filters=[{'Name': "subnet-id", 'Values': list(set([m[2] for m in M]) | set(cleaninputsubnets))}])
    subnets2 = {s['SubnetId']: s for s in subnets1['Subnets']}
    for sub in cleaninputsubnets:
        if not (sub in [m[2] for m in M]):
            if subnets2[sub]['VpcId'] == vpcid:
                rass = []
                for rt in rt2['RouteTables']:
                    for ass in rt['Associations']:
                        if ass['SubnetId'] == sub:
                            rass.append(ass['RouteTableAssociationId'])
                if len(rass) > 0:
                    ec2.disassociate_route_table(AssociationId=rass.pop())
                    print('removed RT association from subnet ', sub)
                RTforS = ec2.create_route_table(VpcId=vpcid)['RouteTable']['RouteTableId']
                ec2.associate_route_table(SubnetId=sub, RouteTableId=RTforS)
                print('created route table ', RTforS, ' and associated it with subnet ', sub)
                M.append(tuple(['NoGW', RTforS, sub, 3]))
            else:
                print('Subnet ', sub, ' is in VPC ', subnets2[sub]['VpcId'], ' which is not in the same vpc as your gateways: (', vpcid, '). Ignoring!')
    
    # Convert to a list and add AZ info into table
    MM = [list(n) for n in set(M)]
    for r in MM:
        r.insert(3, subnets2[r[2]]['AvailabilityZone'])

    return MM
            

def OptimalGWforRT(rt,gwtable,grsaz):
    """ this function decides which gw is optimized for the given route table. If the route table is not in good standing, i.e., it doesn't
    have all the routes or it's pointing to GWs that are not InService, then it returns the GW with the minimal number of subnets associated, 
    in the same AZ, if one exists, or else accross all the GWs. 
    If the RT is already in good standing , then 
     - if it's pointing to GWs that are all in the same AZ as most of the subnets associated with the RT, it evaluates if after dropping this 
    RT from any of the GWs pointed to by the RT, that GW will have still more than the proportion of subnets in the AZ a gw should have. if it does then
    the function returns the GW with the minimal number of subnets in that AZ. 
     - If the RT is pointing to GWs outside its dominant AZ, then  
           - if there is a GW in the AZ, it will reassign it to the one in the AZ with the minimal number of subnets associated with it
           - if there is no GW in this AZ it evaluates whether by dropping this RT from any of the GWs associated with it, the GW will still have more
               than it's proportional share. if it would then it assigns the RT to the GW with the least subnets associated with it
    """   
    
    response = 'Current'
    DRT = Dominant_AZ(grsaz)
    RTIsUp = [g[4] for g in grsaz if g[1] == rt].pop() == 1
    GWsofRT = set([x[0] for x in grsaz if x[1] == rt])  # the set of gateways associated with the RT as someone could add there more than one by mistake
    RTinAZ = sum([1 for y in gwtable if y[0] in GWsofRT and (y[1] != 'InService' or y[2] != DRT[rt])]) == 0  # a boolean to determine if the RT currently already targets all inservice GW in the same AZ
    InAZGWs = [g for g in gwtable if g[1] == 'InService' and g[2] == DRT[rt]]  # the set of GWs Inseervice in the AZ
    RTSubnetCount = {r: sum([1 for rr in grsaz if rr[1] == r]) for r in set([rr[1] for rr in grsaz])}
    GWSubnetCount = {g: sum([1 for rr in grsaz if rr[0] == g]) for g in [gg[0] for gg in gwtable]}
    GWSubnetCountInAZ = {g: sum([1 for rr in grsaz if rr[0] == g and DRT[rr[1]] == DRT[rt]]) for g in [gg[0] for gg in gwtable]}
    AZRTSubnetCount = sum([RTSubnetCount[r] for r in DRT if DRT[r] == DRT[rt]])
    if RTIsUp and RTinAZ and sum([1 for x in InAZGWs if not x[0] in GWsofRT]) > 0:                                       #  if rt has live gws: if rt is in az and has others in az:
        if sum([1 for g in GWsofRT if (GWSubnetCountInAZ[g]-RTSubnetCount[rt]) > AZRTSubnetCount/len(InAZGWs)]) > 0:      # if there are "busier than than average GWs" associated with the route table
            print('route ', rt, 'is servered by GW in the right AZ but they are busier than everage.')
            M = [[GWSubnetCount[x[0]], x[0]] for x in InAZGWs if (GWSubnetCount[x[0]] + RTSubnetCount[rt]) < max([GWSubnetCount[x] for x in GWsofRT])]
            if len(M) > 0:
                response = min(M)[1]
            else:
                print('no gw is sufficiently better to warrant a gw switch for route table ', rt)
            
    elif RTIsUp and not RTinAZ and sum([1 for x in InAZGWs if not x[0] in GWsofRT]) == 0:       
        if sum([1 for g in GWsofRT if (GWSubnetCount[g] - RTSubnetCount[rt]) > len(set([x[2] for x in grsaz]))/len([g for g in gwtable if g[1] == 'InService'])]) > 0:
            print('route ', rt, 'has a busy GW in another AZ and there are no GWs in its AZ . Looking for less busy GWs')
            M = [[GWSubnetCount[x[0]], x[0]] for x in gwtable if x[1] == 'InService' and GWSubnetCount[x[0]] + RTSubnetCount[rt] < max([GWSubnetCount[x] for x in GWsofRT])]
            if len(M) > 0:
                response = min(M)[1]
            else:
                print('no gw is sufficiently better to warrant a gw switch for route ', rt)
        else:
            print('Although served by a gw outside its AZ, no gw is sufficiently better to warrant a gw switch for route table ', rt) 

    elif ((RTIsUp and not RTinAZ) or not RTIsUp) and sum([1 for x in InAZGWs]) > 0:
        print('route ', rt, 'is not served by GW in the right AZ and there are alternative GWs in AZ.')
        M = [[GWSubnetCount[x[0]], x[0]] for x in InAZGWs]
        response = min(M)[1]

    elif not RTIsUp:
        print('route ', rt, 'has GWs OutofService or is missing routes. There are GWs in Service outside its AZ')
        M = [[GWSubnetCount[x[0]], x[0]] for x in gwtable if x[1] == 'InService']
        response = min(M)[1]

    return response


def Dominant_AZ(grsaz):
    """Takes the GRSAZ list and returns another dict with the dominant AZ for reach route table
    """

    M = {}
    RTs = list(set([y[1] for y in grsaz]))
    for rt in RTs:
        AZs = list(set([y[3] for y in grsaz if y[1] == rt]))
        MM = []
        for az in AZs:
            MM.append([az, sum([1 for y in grsaz if (y[1] == rt and y[3] == az)])])
        mini = min([m[0] for m in MM if m[1] == max([m[1] for m in MM])])  #we take the first AZ that has the maximal number of subnets
        M.update({rt: mini})
    return M


def ReplaceGWsforRTinGRSAZ(gwo, gwi, grsaz, rtid):
    """Changes one GW for another in the grsaz table for a given route
    """
    M = grsaz
    for r in M:
        if r[0] == gwo and r[1] == rtid:
            r[0] = gwi
    return M


def ReplaceGWforRTinAWS(gwo, gwi, grsaz, rtid, gwtable, Routetargets):
    """changes one GW for another in the all the routes of a given route table in AWS
    """
    ec2 = boto3.client('ec2')
    # rtd=ec2.describe_route_tables(RouteTableIds=[rtid])
    if [g[4] for g in grsaz if g[0] == gwo and g[1] == rtid].pop() < 3:
        for s in Routetargets:
            ec2.replace_route(DestinationCidrBlock=s, RouteTableId=rtid, NetworkInterfaceId=[gw[4][len(gw[4]) - 1][1] for gw in gwtable if gw[0] == gwi].pop())
        print('in route table:', rtid, ' replacing gw: ', gwo, ' with gw: ', gwi)
    else:
        for s in Routetargets:
            ec2.create_route(DestinationCidrBlock=s, RouteTableId=rtid, NetworkInterfaceId=[gw[4][len(gw[4]) - 1][1] for gw in gwtable if gw[0] == gwi].pop())
        print('in route table:', rtid, ' added new routes with gw: ', gwi)

